"""Sketch generation for a confirmed wiring plan.

Claude writes the Arduino code. Everything it is allowed to use (pins, LED polarity, logging
object, parameter values) is taken from the wiring plan and the hardware database and handed to
it as a CodegenContext. Each answer goes through three gates: the response schema, the static
checks in codecheck.py, and a compile with the real UNO Q core. A failed gate triggers a repair
request with the problems attached, at most MAX_REPAIRS times. If no AI attempt passes, or
there is no AI, the rule-based template is used instead and labelled as such.

Nothing here uploads to a board.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from pydantic import ValidationError

from . import codecheck, demo, facts
from .compiler import ArduinoCliCompiler
from .llm import ask_json, make_client
from .models import (
    Behavior, CodegenContext, CodeIssue, InputPin, CompileReport, CompileStatus, GeneratedSketch, GenerationAttempt,
    ProjectSpec, SerialEvent, WiringStep,
)

log = logging.getLogger("learnlens.codegen")

EFFORT = os.getenv("LEARNLENS_CODEGEN_EFFORT", "medium")
MAX_REPAIRS = 2
SKETCH_NAME = "learnlens_sketch"  # arduino-cli needs <name>/<name>.ino
PROJECTS_DIR = Path(os.getenv("LEARNLENS_PROJECTS_DIR", Path(__file__).resolve().parents[2] / "generated"))
REQUIRED_EVENTS = ["READY", "SOUND_DETECTED", "LED_ON", "LED_OFF"]
LEVEL_METER_EVENTS = ["READY", "LOUD", "QUIET"]  # LOUD/QUIET let the test step see that the meter reacts
DEFAULT_DEBOUNCE_MS = 300
DEFAULT_SAMPLE_WINDOW_MS = 25

SKETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "source_code": {"type": "string"},
        "explanation": {"type": "string"},
        "display_summary": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "expected_serial_events": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"event": {"type": "string"}, "when": {"type": "string"}},
                "required": ["event", "when"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["source_code", "explanation", "display_summary", "assumptions", "expected_serial_events"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You write Arduino sketches for LearnLens, a tool that guides beginners through small
hardware builds. The user has already wired the parts by following a confirmed wiring plan. Your
sketch is checked by a static analyzer, compiled with the board's real core, and shown to the user,
who must approve it before anything is uploaded. A beginner will read it, so favour plain, short,
commented code over clever code.

The board is an Arduino UNO Q. It is not a classic Arduino UNO: its microcontroller is an STM32U585
running the Arduino core on Zephyr, and several habits from the classic UNO are wrong here. The
BOARD FACTS in the request come from a verified hardware database. Treat them as the only truth
about pins, levels and logging, even where they contradict what you remember about Arduino boards.

The request lists what the static analyzer rejects for this project. Each rule exists because the
thing it forbids either touches hardware outside the wiring plan or can hang the board.

Return source_code (the complete .ino file), explanation (3-5 sentences a beginner can follow),
display_summary (how the finished build behaves and why, in at most 2 short sentences and under 160
characters, shown on the glasses right before the upload),
assumptions (each thing that must be true about the hardware for the sketch to work), and
expected_serial_events (each log line and when it appears)."""


def signal_inputs(steps: List[WiringStep]) -> List[InputPin]:
    """Every sensor signal in the confirmed wiring, in wiring order."""
    cat = facts.catalog()
    inputs = []
    for step in steps:
        c = step.connection
        if c is None:
            continue
        pin = cat[c.from_pin.component]["pins"][c.from_pin.pin]
        if pin["kind"] not in ("digital_out", "analog_out"):
            continue
        header, analog = c.to_pin.pin, pin["kind"] == "analog_out"
        board_pin = cat[c.to_pin.component]["pins"][header]
        if board_pin["kind"] != "gpio" or bool(board_pin.get("analog")) != analog or not re.fullmatch(r"[AD]\d+", header):
            raise ValueError(f"{header} is not a usable {'analog' if analog else 'digital'} header pin")
        part = cat[c.from_pin.component]
        several = len((part.get("add_on") or {}).get("signals", [])) > 1  # then the pin says which signal this is
        inputs.append(InputPin(component=c.from_pin.component,
                               name=part["name"] + (f", its {c.from_pin.pin} signal" if several else ""), header=header,
                               number=None if analog else int(header[1:]), kind="analog" if analog else "digital",
                               active_level=pin.get("active_level", "")))
    return inputs


def build_context(spec: ProjectSpec, steps: List[WiringStep], sensor_active_level: Optional[str] = None,
                  debounce_ms: Optional[int] = None, sample_window_ms: Optional[int] = None,
                  previous_code: str = "") -> CodegenContext:
    """Derive what the generator may use from the confirmed plan and the hardware database."""
    cat = facts.catalog()
    board = cat[spec.board]
    inputs = signal_inputs(steps)
    primary = inputs[0]
    analog, custom = primary.kind == "analog", spec.behavior == Behavior.custom
    led, logging_, matrix = board["builtin_led"], board["logging"], board["led_matrix"]
    use_matrix = analog or custom

    by_id = {f["id"]: f["statement"] for c in cat.values() for f in c["facts"]}
    quoted: List[str] = []
    for step in steps:  # the facts behind every signal connection
        if step.connection and cat[step.connection.from_pin.component]["pins"][step.connection.from_pin.pin]["kind"] in (
                "digital_out", "analog_out"):
            quoted += [board["pins"][step.connection.to_pin.pin]["fact"],
                       cat[step.connection.from_pin.component]["pins"][step.connection.from_pin.pin]["fact"]]
    quoted += (matrix["facts"] if use_matrix else []) + (led["facts"] if custom or not analog else [])
    quoted += [*logging_["facts"], "unoq-fqbn"]
    return CodegenContext(
        project=spec, board_name=board["name"], fqbn=board["fqbn"]["value"], sensor_name=primary.name,
        signal_kind=primary.kind, output="led_matrix" if analog else "builtin_led",
        sensor_pin=primary.header, sensor_pin_number=primary.number,
        sensor_active_level=sensor_active_level or primary.active_level or "HIGH",
        debounce_ms=debounce_ms or DEFAULT_DEBOUNCE_MS, sample_window_ms=sample_window_ms or DEFAULT_SAMPLE_WINDOW_MS,
        led_symbol=led["symbol"], led_on_level=led["on_level"],
        matrix_type=matrix["type"] if use_matrix else "", matrix_rows=matrix["rows"] if use_matrix else 0,
        matrix_columns=matrix["columns"] if use_matrix else 0,
        matrix_max_brightness=matrix["max_brightness"] if use_matrix else 0,
        inputs=inputs, custom_behavior=spec.custom_behavior if custom else "", previous_code=previous_code if custom else "",
        log_object=logging_["object"], log_setup=logging_["setup"],
        allowed_includes=[logging_["include"]] + ([matrix["include"]] if use_matrix else []),
        required_events=["READY"] if custom else LEVEL_METER_EVENTS if analog else REQUIRED_EVENTS,
        facts=[by_id[f] for f in dict.fromkeys(quoted)])


COMMON_RULES = """- uses a pin other than the approved one, or passes a pin that is not a named constant or literal
- includes a header that is not on the approved list, or uses any preprocessor directive except #include
- uses do or goto, or a delay longer than 50 ms
- uses interrupts, analogWrite or tone, dynamic memory, registers, Zephyr or HAL calls, Bridge RPC calls,
  Serial, or any other bus or peripheral"""


def task_prompt(ctx: CodegenContext) -> str:
    head = f"""PROJECT: {ctx.project.title}. {ctx.project.summary}

BOARD FACTS (verified; the only truth about this hardware):
{chr(10).join("- " + f for f in ctx.facts)}
"""
    includes = ", ".join("<" + h + ">" for h in ctx.allowed_includes)
    logging_ = f"{ctx.log_object}.println(...), started in setup() with: {' '.join(ctx.log_setup)}"
    if ctx.custom_behavior:
        return head + custom_task(ctx, includes, logging_)
    if ctx.signal_kind == "analog":
        return head + f"""
BEHAVIOR: A sound level meter. The louder it is, the more columns of the LED matrix light up, from
left to right. Silence shows nothing. The bar rises at once and falls back gently.

APPROVED HARDWARE ACCESS (nothing else may be touched):
- Sensor signal: {ctx.sensor_name} analog output on header {ctx.sensor_pin}. Read it with analogRead({ctx.sensor_pin}). No pinMode needed.
- Output: the LED matrix, {ctx.matrix_rows} rows by {ctx.matrix_columns} columns, brightness 0 to {ctx.matrix_max_brightness}, through {ctx.matrix_type}.
- Approved #include: {includes}
- Logging: {logging_}

REQUIRED PARAMETER (declare once near the top as a const, with exactly this value, and use it):
- const unsigned long SAMPLE_WINDOW_MS = {ctx.sample_window_ms};   // length of one loudness measurement

REQUIRED LOGIC:
- The microphone signal swings around a resting level. Loudness is the highest minus the lowest
  analogRead value within one SAMPLE_WINDOW_MS window, timed with millis().
- Do not assume an ADC range or a fixed threshold: track the quietest and the loudest swing seen and
  scale between them, so the meter calibrates itself.
- for and while loops are allowed here. Every while loop must end by time (millis()).

REQUIRED LOG LINES (each exactly this text on its own line):
- READY   once, at the end of setup()
- LOUD    once each time the bar reaches at least half of the matrix
- QUIET   once each time the bar has fallen back to empty after a LOUD
  Print them only on these changes, never on every pass through loop().

THE STATIC ANALYZER REJECTS A SKETCH THAT:
{COMMON_RULES}
- uses digitalRead, digitalWrite or pinMode on any pin, or analogRead on any pin but {ctx.sensor_pin}"""

    behavior = {
        Behavior.toggle_on_event: "Each new sound event flips the LED: off -> on -> off.",
        Behavior.on_while_active: "The LED is on while the sensor reports sound and off otherwise.",
    }[ctx.project.behavior]
    off = "HIGH" if ctx.led_on_level == "LOW" else "LOW"
    return head + f"""
BEHAVIOR: {behavior}

APPROVED HARDWARE ACCESS (nothing else may be touched):
- Sensor signal: {ctx.sensor_name} digital output on header {ctx.sensor_pin}, Arduino pin number {ctx.sensor_pin_number}. pinMode INPUT, read with digitalRead.
- Output: {ctx.led_symbol}. pinMode OUTPUT. {ctx.led_on_level} turns it on, {off} turns it off. Start with it off.
- Approved #include: {includes}
- Logging: {logging_}

REQUIRED PARAMETERS (declare each once near the top as a const, with exactly these values, and use them):
- const int SENSOR_ACTIVE_LEVEL = {ctx.sensor_active_level};   // level on the pin while the sensor reports sound
- const unsigned long DEBOUNCE_MS = {ctx.debounce_ms};   // after an event, ignore new events for this long

REQUIRED LOGIC:
- Edge detection: react when the pin changes from inactive to SENSOR_ACTIVE_LEVEL, not while it stays active.
- Debounce with millis() and DEBOUNCE_MS, without blocking.

REQUIRED LOG LINES (each exactly this text on its own line):
- READY            once, at the end of setup()
- SOUND_DETECTED   on each accepted sound event
- LED_ON / LED_OFF whenever the LED changes, after the change

THE STATIC ANALYZER REJECTS A SKETCH THAT:
{COMMON_RULES}
- sets the sensor pin to anything but INPUT, or writes to it
- uses while or for (loop() must poll and return), or any analog I/O"""


def custom_task(ctx: CodegenContext, includes: str, logging_: str) -> str:
    """The task for a build that was developed further: several inputs, behaviour in the user's words."""
    off = "HIGH" if ctx.led_on_level == "LOW" else "LOW"
    lines = []
    for i in ctx.inputs:
        if i.kind == "analog":
            lines.append(f"- Input: {i.name}, analog output on header {i.header}. Read it with analogRead({i.header}) only. "
                         "What its voltage means is in the facts below; do not assume an ADC range or a resting level.")
        else:
            level = (f"It reads {i.active_level} while the sensor reports its event." if i.active_level else
                     "No level is documented for it: react to changes of the level, as the facts below describe.")
            lines.append(f"- Input: {i.name}, digital output on header {i.header}, Arduino pin number {i.number}. "
                         f"pinMode INPUT, read with digitalRead. {level}")
    previous = (f"\n\nTHE SKETCH THAT IS RUNNING NOW (it works; keep what should stay, change what the new behaviour "
                f"needs):\n{ctx.previous_code}" if ctx.previous_code else "")
    return f"""
THE BUILD WAS DEVELOPED FURTHER. WHAT IT MUST DO NOW:
{ctx.custom_behavior}

HOW IT GOT HERE (the user's requests, oldest first):
{chr(10).join("- " + r for r in ctx.project.requests) or "- (none)"}

APPROVED HARDWARE ACCESS (nothing else may be touched):
{chr(10).join(lines)}
- Output: the LED matrix, {ctx.matrix_rows} rows by {ctx.matrix_columns} columns, brightness 0 to {ctx.matrix_max_brightness}, through {ctx.matrix_type}.
- Output: {ctx.led_symbol}. pinMode OUTPUT. {ctx.led_on_level} turns it on, {off} turns it off.
- Approved #include: {includes}
- Logging: {logging_}

REQUIRED:
- Read every input listed above at least once.
- Print READY once at the end of setup(), exactly this text on its own line.
- for and while loops are allowed. Every while loop must end by time (millis()), so loop() keeps running.

THE STATIC ANALYZER REJECTS A SKETCH THAT:
{COMMON_RULES}
- reads an analog input with anything but analogRead, or uses analogRead on any other pin
- sets a digital input to anything but INPUT, or writes to it
- uses digitalWrite on anything but {ctx.led_symbol}{previous}"""


def repair_prompt(ctx: CodegenContext, previous: str, problems: str) -> str:
    return f"""{task_prompt(ctx)}

YOUR PREVIOUS SKETCH WAS REJECTED. Fix every problem below and return the complete corrected sketch.
Keep everything that was not part of a problem.

PROBLEMS:
{problems}

PREVIOUS SKETCH:
{previous}"""


def template_sketch(ctx: CodegenContext) -> GeneratedSketch:
    """Rule-based sketch for the same contract. Used without AI, and when no AI attempt passes."""
    if ctx.custom_behavior:
        return gated_level_meter_template(ctx)
    if ctx.signal_kind == "analog":
        return level_meter_template(ctx)
    off = "HIGH" if ctx.led_on_level == "LOW" else "LOW"
    title = " ".join(re.sub(r"[^\w .,'-]", " ", ctx.project.title).split())[:60]
    if ctx.project.behavior == Behavior.toggle_on_event:
        reaction = """\
  // Edge detection: act only when the pin has just become active, and not too soon after the last event.
  if (active && !wasActive && now - lastEventMs >= DEBOUNCE_MS) {
    lastEventMs = now;
    ledOn = !ledOn;
    Monitor.println("SOUND_DETECTED");
    digitalWrite(LED_PIN, ledOn ? LED_ON_LEVEL : LED_OFF_LEVEL);
    Monitor.println(ledOn ? "LED_ON" : "LED_OFF");
  }"""
        does = "Each new sound event flips the LED."
    else:
        reaction = """\
  // Edge detection: act only when the pin changes, and not too soon after the last change.
  if (active != wasActive && now - lastEventMs >= DEBOUNCE_MS) {
    lastEventMs = now;
    ledOn = active;
    if (active) {
      Monitor.println("SOUND_DETECTED");
    }
    digitalWrite(LED_PIN, ledOn ? LED_ON_LEVEL : LED_OFF_LEVEL);
    Monitor.println(ledOn ? "LED_ON" : "LED_OFF");
  }"""
        does = "The LED is on while the sensor reports sound."
    code = f"""\
// LearnLens: {title}
// Board: {ctx.board_name} (FQBN {ctx.fqbn}). Not a classic Arduino UNO.
// Wiring: sensor DO -> {ctx.sensor_pin}, + -> +3V3, G -> GND.
#include <{ctx.allowed_includes[0]}>

const int SENSOR_PIN = {ctx.sensor_pin_number};  // header {ctx.sensor_pin}, input only
const int LED_PIN = {ctx.led_symbol};
const int LED_ON_LEVEL = {ctx.led_on_level};  // this board's LED lights at {ctx.led_on_level}
const int LED_OFF_LEVEL = {off};
const int SENSOR_ACTIVE_LEVEL = {ctx.sensor_active_level};  // level on SENSOR_PIN while the sensor reports sound
const unsigned long DEBOUNCE_MS = {ctx.debounce_ms};  // after an event, ignore new events for this long

bool ledOn = false;
bool wasActive = false;
unsigned long lastEventMs = 0;

void setup() {{
  pinMode(SENSOR_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LED_OFF_LEVEL);
  {" ".join(ctx.log_setup)}
  {ctx.log_object}.println("READY");
}}

void loop() {{
  bool active = digitalRead(SENSOR_PIN) == SENSOR_ACTIVE_LEVEL;
  unsigned long now = millis();
{reaction.replace("Monitor", ctx.log_object)}
  wasActive = active;
}}
"""
    return GeneratedSketch(
        source_code=code,
        display_summary="Each clap flips the board's LED. After a clap the sensor is ignored briefly, so one "
                        "clap counts once.",
        explanation=(f"The sketch reads the sensor's digital output on {ctx.sensor_pin} every pass through loop(). "
                     f"{does} It reacts only to the moment the signal becomes active and then ignores further "
                     f"events for {ctx.debounce_ms} ms, so one clap counts once. Status lines are printed through "
                     f"{ctx.log_object}."),
        assumptions=[
            f"The sensor's DO pin is {ctx.sensor_active_level} while it reports sound. If the LED reacts to silence "
            "instead, regenerate with the other sensor_active_level.",
            "The sensor's trim potentiometer is set so that DO is inactive in a quiet room.",
            f"{ctx.led_symbol} is the board's red LED3 and lights at {ctx.led_on_level}.",
            f"Log lines are visible only where {ctx.log_object} output is shown (App Lab or IDE console).",
        ],
        expected_serial_events=[
            SerialEvent(event="READY", when="once after power-up, when setup() has finished"),
            SerialEvent(event="SOUND_DETECTED", when="each time a sound event is accepted"),
            SerialEvent(event="LED_ON", when="right after the LED was switched on"),
            SerialEvent(event="LED_OFF", when="right after the LED was switched off"),
        ])


def level_meter_template(ctx: CodegenContext) -> GeneratedSketch:
    title = " ".join(re.sub(r"[^\w .,'-]", " ", ctx.project.title).split())[:60]
    code = f"""\
// LearnLens: {title}
// Board: {ctx.board_name} (FQBN {ctx.fqbn}). Not a classic Arduino UNO.
// Wiring: sensor AO -> {ctx.sensor_pin}, + -> +3V3, G -> GND. The sensor must stay on 3.3 V.
#include <{ctx.allowed_includes[0]}>
#include <{ctx.allowed_includes[1]}>

const int MIC_PIN = {ctx.sensor_pin};
const int ROWS = {ctx.matrix_rows};
const int COLUMNS = {ctx.matrix_columns};
const int BRIGHTEST = {ctx.matrix_max_brightness};
const unsigned long SAMPLE_WINDOW_MS = {ctx.sample_window_ms};  // length of one loudness measurement
const int MIN_RANGE = 12;  // smallest loud-quiet span, so silence does not flicker

{ctx.matrix_type} matrix;
uint8_t frame[{ctx.matrix_rows * ctx.matrix_columns}];

float quiet = 1e9f;  // smallest swing seen: the room's noise floor
float loud = 0;      // largest swing seen recently
float shown = 0;     // bar length on the matrix, in columns
bool wasLoud = false;  // for the LOUD / QUIET log lines

void setup() {{
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();
  {" ".join(ctx.log_setup)}
  {ctx.log_object}.println("READY");
}}

void loop() {{
  // The microphone signal swings around a resting level. Loudness = how far it swings in one window.
  int lowest = 100000;
  int highest = 0;
  unsigned long start = millis();
  while (millis() - start < SAMPLE_WINDOW_MS) {{
    int value = analogRead(MIC_PIN);
    if (value < lowest) lowest = value;
    if (value > highest) highest = value;
  }}
  float swing = highest - lowest;

  // Self-calibration: the floor creeps up slowly, the ceiling creeps down slowly.
  if (swing < quiet) quiet = swing; else quiet += 0.002f * (swing - quiet);
  if (swing > loud) loud = swing; else loud -= 0.01f * (loud - quiet);
  float range = loud - quiet;
  if (range < MIN_RANGE) range = MIN_RANGE;

  // 0 = silence, COLUMNS = as loud as it has been. Rise at once, fall gently.
  float level = (swing - quiet) / range * COLUMNS;
  if (level < 0) level = 0;
  if (level > COLUMNS) level = COLUMNS;
  if (level > shown) shown = level; else shown -= 0.6f;
  if (shown < 0) shown = 0;

  // Light whole columns up to the level; the last, partly reached column is dimmer.
  int full = (int)shown;
  int partial = (int)((shown - full) * BRIGHTEST);
  for (int row = 0; row < ROWS; row++) {{
    for (int column = 0; column < COLUMNS; column++) {{
      uint8_t brightness = 0;
      if (column < full) brightness = BRIGHTEST;
      else if (column == full) brightness = partial;
      frame[row * COLUMNS + column] = brightness;
    }}
  }}
  matrix.draw(frame);

  // Report once when it gets loud and once when it is quiet again.
  if (!wasLoud && shown >= COLUMNS / 2) {{
    wasLoud = true;
    {ctx.log_object}.println("LOUD");
  }} else if (wasLoud && shown < 1) {{
    wasLoud = false;
    {ctx.log_object}.println("QUIET");
  }}
}}
"""
    return GeneratedSketch(
        source_code=code,
        display_summary="The microphone's signal is measured many times a second. The louder it is, the more "
                        "columns of the LED matrix light up.",
        explanation=(f"The sketch reads the microphone's analog output on {ctx.sensor_pin} many times during a short "
                     f"window of {ctx.sample_window_ms} ms and takes the difference between the highest and lowest "
                     "reading as the loudness. It keeps track of the quietest and loudest values it has seen and "
                     "scales between them, so it adjusts to the room by itself. The loudness is drawn on the board's "
                     "LED matrix as a bar that grows from left to right."),
        assumptions=[
            f"The sensor's AO pin is wired to {ctx.sensor_pin} and the sensor is powered from +3V3, never from 5 V.",
            "The matrix shows the board's boot animation for about half a minute after power-up before the meter appears.",
            "The first seconds are used for calibration, so the bar can jump at the start.",
            f"The READY line is visible only where {ctx.log_object} output is shown (App Lab or IDE console).",
        ],
        expected_serial_events=[
            SerialEvent(event="READY", when="once after power-up, when setup() has finished"),
            SerialEvent(event="LOUD", when="each time the bar reaches at least half of the matrix"),
            SerialEvent(event="QUIET", when="each time the bar has fallen back to empty after that"),
        ])


def display_summary(sketch: GeneratedSketch, limit: int = 180) -> str:
    """The short text for the glasses: the model's summary, or the start of its explanation."""
    text = " ".join((sketch.display_summary or "").split())
    if not text:
        sentences = re.split(r"(?<=[.!?])\s+", " ".join(sketch.explanation.split()))
        text = " ".join(sentences[:2])
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


def gated_level_meter_template(ctx: CodegenContext) -> GeneratedSketch:
    """Prevalidated sketches for the level meter plus one digital sensor, in the variants listed in
    demo.EXTENSION_VARIANTS. Any other change needs the AI service."""
    kinds = [i.kind for i in ctx.inputs]
    if kinds != ["analog", "digital"]:
        raise RuntimeError("This change needs the AI service: there is no prevalidated sketch for it.")
    gate = ctx.inputs[1]
    variant = demo.variant_for(ctx.custom_behavior)
    base = level_meter_template(ctx)
    code = base.source_code

    def patch(old: str, new: str) -> None:
        nonlocal code
        assert code.count(old) == 1, old
        code = code.replace(old, new)

    patch(f"const int MIC_PIN = {ctx.sensor_pin};\n",
          f"const int MIC_PIN = {ctx.sensor_pin};\n"
          f"const int GATE_PIN = {gate.number};  // header {gate.header}: {gate.name}\n"
          f"const int GATE_ACTIVE_LEVEL = {gate.active_level};  // level while the sensor detects something\n")
    patch("  matrix.clear();\n", "  matrix.clear();\n  pinMode(GATE_PIN, INPUT);\n")
    patch("// Wiring: sensor AO", f"// Wiring: {gate.name.split()[0]} OUT -> {gate.header}; microphone AO")
    if variant == "freeze":
        patch("  if (level > shown) shown = level; else shown -= 0.6f;\n",
              "  // While the second sensor detects something, the bar is frozen where it is.\n"
              "  if (digitalRead(GATE_PIN) != GATE_ACTIVE_LEVEL) {\n"
              "    if (level > shown) shown = level; else shown -= 0.6f;\n"
              "  }\n")
        new = "While it detects something, the bar is not updated and stays where it is."
    elif variant == "toggle":
        patch("bool wasLoud = false;  // for the LOUD / QUIET log lines\n",
              "bool wasLoud = false;  // for the LOUD / QUIET log lines\n"
              "bool meterOn = true;         // switched by the second sensor\n"
              "bool gateWasActive = false;  // to notice the moment something appears\n")
        patch("  // Light whole columns up to the level",
              "  // Each time something newly appears in front of the second sensor, switch the display on or off.\n"
              "  bool gateActive = digitalRead(GATE_PIN) == GATE_ACTIVE_LEVEL;\n"
              "  if (gateActive && !gateWasActive) meterOn = !meterOn;\n"
              "  gateWasActive = gateActive;\n"
              "  if (!meterOn) shown = 0;\n\n"
              "  // Light whole columns up to the level")
        new = "Each time it newly detects something, the display is switched on or off."
    else:
        patch("  // Light whole columns up to the level",
              "  // Show the meter only while the second sensor detects something.\n"
              "  if (digitalRead(GATE_PIN) != GATE_ACTIVE_LEVEL) shown = 0;\n\n"
              "  // Light whole columns up to the level")
        new = "While it detects nothing, the bar is forced to zero and the matrix stays dark."
    return GeneratedSketch(
        source_code=code,
        display_summary=demo.EXTENSION_VARIANTS[variant]["summary"],
        explanation=base.explanation + f" New: the sketch also reads the {gate.name} on {gate.header}. {new}",
        assumptions=base.assumptions + [
            f"The {gate.name} reads {gate.active_level} on {gate.header} while it detects something.",
            "Both sensors share the board's single +3V3 pin.",
        ],
        expected_serial_events=base.expected_serial_events)


def check_response(sketch: GeneratedSketch, ctx: CodegenContext) -> List[CodeIssue]:
    issues = codecheck.check_sketch(sketch.source_code, ctx)
    listed = {e.event for e in sketch.expected_serial_events}
    for event in ctx.required_events:
        if event not in listed:
            issues.append(CodeIssue(check="schema", rule="missing_expected_event",
                                    message=f"expected_serial_events does not list {event}"))
    return issues


def save_project(session_id: str, attempt: int, sketch: GeneratedSketch, ctx: CodegenContext) -> Path:
    """Write the sketch to its own directory: generated/<session>/attempt-<n>/learnlens_sketch/."""
    if not re.fullmatch(r"[0-9a-f]{6,32}", session_id):
        raise ValueError("bad session id")
    sketch_dir = PROJECTS_DIR / session_id / f"attempt-{attempt}" / SKETCH_NAME
    sketch_dir.mkdir(parents=True, exist_ok=True)
    (sketch_dir / f"{SKETCH_NAME}.ino").write_text(sketch.source_code)
    (sketch_dir.parent / "generation.json").write_text(json.dumps(
        {"context": ctx.model_dump(mode="json"), "response": sketch.model_dump(mode="json")}, indent=2))
    return sketch_dir


class CodeGenerator:
    def __init__(self, mode: Optional[str] = None, client=None, compiler=None) -> None:
        default = "demo" if demo.enabled() else os.getenv("LEARNLENS_CODEGEN") or os.getenv("LEARNLENS_TUTOR", "auto")
        self.mode = mode or default  # auto | claude | mock | demo
        self.compiler = compiler or ArduinoCliCompiler()
        self.client = client
        if self.client is None and self.mode in ("auto", "claude"):
            try:
                self.client = make_client(timeout=120)
            except Exception as e:  # SDK missing or no credentials
                if self.mode == "claude":
                    raise
                log.warning("Claude unavailable, sketches come from the template: %s", e)

    def _ask(self, prompt: str) -> Tuple[Optional[GeneratedSketch], List[CodeIssue]]:
        try:
            raw = ask_json(self.client, [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
                           [{"type": "text", "text": prompt}], SKETCH_SCHEMA, EFFORT, max_tokens=16000)
            return GeneratedSketch.model_validate(raw), []
        except (ValidationError, json.JSONDecodeError, StopIteration) as e:
            return None, [CodeIssue(check="schema", rule="invalid_response",
                                    message=f"the answer did not match the response schema: {str(e)[:300]}")]
        except Exception as e:
            log.exception("code generation call failed: %s", e)
            return None, [CodeIssue(check="schema", rule="llm_error", message=f"{type(e).__name__}: {str(e)[:300]}")]

    def _gate(self, session_id: str, number: int, kind: str, sketch: Optional[GeneratedSketch],
              issues: List[CodeIssue], ctx: CodegenContext, stage: Callable[[str], None]) -> GenerationAttempt:
        """Static checks, then save, then compile. A sketch that fails the static checks is never compiled."""
        attempt = GenerationAttempt(attempt=number, kind=kind, issues=list(issues))
        if sketch is None:
            return attempt
        stage("Checking pins and safety rules")
        attempt.issues += check_response(sketch, ctx)
        attempt.project_dir = str(save_project(session_id, number, sketch, ctx))
        if not attempt.issues:
            stage(f"Compiling for the {ctx.board_name}")
            attempt.compile = self.compiler.compile(Path(attempt.project_dir), ctx.fqbn)
            if attempt.compile.status == CompileStatus.failed:
                attempt.issues.append(CodeIssue(check="compile", rule="compile_error",
                                                message=codecheck.compile_errors(attempt.compile.log)))
        return attempt

    def generate(self, session_id: str, ctx: CodegenContext, on_stage: Optional[Callable[[str], None]] = None
                 ) -> Tuple[GeneratedSketch, str, List[GenerationAttempt]]:
        """Returns (sketch, source, attempts) with source in llm | template | demo. Raises if even the template fails."""
        attempts: List[GenerationAttempt] = []
        stage = on_stage or (lambda text: None)
        if self.client is not None:
            prompt = task_prompt(ctx)
            for number in range(MAX_REPAIRS + 1):
                stage("Claude is writing the code" if number == 0 else f"Claude is fixing a problem (try {number + 1})")
                sketch, issues = self._ask(prompt)
                attempt = self._gate(session_id, number, "generate" if number == 0 else "repair", sketch, issues, ctx,
                                     stage)
                attempts.append(attempt)
                if not attempt.issues:
                    return sketch, "llm", attempts
                if any(i.rule == "llm_error" for i in attempt.issues):
                    break  # the service is down: asking again will not help
                previous = sketch.source_code if sketch else "(no valid sketch was returned)"
                problems = "\n".join(i.message if i.check == "compile" else codecheck.summarize([i])
                                     for i in attempt.issues)
                prompt = repair_prompt(ctx, previous, problems)

        stage("Loading the prevalidated code")
        sketch = template_sketch(ctx)  # raises RuntimeError when there is none for this change
        attempt = self._gate(session_id, len(attempts), "template", sketch, [], ctx, stage)
        attempts.append(attempt)
        if attempt.issues:
            raise RuntimeError("the template sketch failed its own checks: " + codecheck.summarize(attempt.issues))
        return sketch, "demo" if self.mode == "demo" else "template", attempts


def final_compile(attempts: List[GenerationAttempt], fqbn: str) -> CompileReport:
    return attempts[-1].compile or CompileReport(status=CompileStatus.not_run, fqbn=fqbn)
