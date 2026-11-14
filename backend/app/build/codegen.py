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
from typing import List, Optional, Tuple

from pydantic import ValidationError

from . import codecheck, demo, facts
from .compiler import ArduinoCliCompiler
from .llm import ask_json, make_client
from .models import (
    Behavior, CodegenContext, CodeIssue, CompileReport, CompileStatus, GeneratedSketch, GenerationAttempt,
    ProjectSpec, SerialEvent, WiringStep,
)

log = logging.getLogger("learnlens.codegen")

EFFORT = os.getenv("LEARNLENS_CODEGEN_EFFORT", "medium")
MAX_REPAIRS = 2
SKETCH_NAME = "learnlens_sketch"  # arduino-cli needs <name>/<name>.ino
PROJECTS_DIR = Path(os.getenv("LEARNLENS_PROJECTS_DIR", Path(__file__).resolve().parents[2] / "generated"))
REQUIRED_EVENTS = ["READY", "SOUND_DETECTED", "LED_ON", "LED_OFF"]
DEFAULT_DEBOUNCE_MS = 300

SKETCH_SCHEMA = {
    "type": "object",
    "properties": {
        "source_code": {"type": "string"},
        "explanation": {"type": "string"},
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
    "required": ["source_code", "explanation", "assumptions", "expected_serial_events"],
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

The static analyzer rejects a sketch that does any of the following, because each one either
touches hardware outside the wiring plan or can hang the board:
- uses a pin other than the approved ones, or passes a pin that is not a named constant or literal
- sets the sensor pin to anything but INPUT, or writes to it
- includes a header that is not on the approved list, or uses any preprocessor directive except #include
- uses while, for, do or goto, or a delay longer than 50 ms (loop() must poll and return)
- uses interrupts, analog I/O, dynamic memory, registers, Zephyr or HAL calls, Bridge RPC calls,
  Serial, or any other bus or peripheral

Return source_code (the complete .ino file), explanation (3-5 sentences a beginner can follow),
assumptions (each thing that must be true about the hardware for the sketch to work), and
expected_serial_events (each log line and when it appears)."""


def build_context(spec: ProjectSpec, steps: List[WiringStep], sensor_active_level: Optional[str] = None,
                  debounce_ms: Optional[int] = None) -> CodegenContext:
    """Derive what the generator may use from the confirmed plan and the hardware database."""
    cat = facts.catalog()
    board, sensor = cat[spec.board], cat[spec.input_component]
    signal = next(s.connection for s in steps if s.connection
                  and sensor["pins"][s.connection.from_pin.pin]["kind"] == "digital_out")
    header = signal.to_pin.pin
    if not re.fullmatch(r"D\d+", header) or board["pins"][header]["kind"] != "gpio":
        raise ValueError(f"{header} is not a digital header pin")
    led, logging_ = board["builtin_led"], board["logging"]
    by_id = {f["id"]: f["statement"] for f in board["facts"] + sensor["facts"]}
    quoted = [board["pins"][header]["fact"], *led["facts"], *logging_["facts"], "unoq-fqbn",
              sensor["pins"][signal.from_pin.pin]["fact"]]
    return CodegenContext(
        project=spec, board_name=board["name"], fqbn=board["fqbn"]["value"], sensor_name=sensor["name"],
        sensor_pin=header, sensor_pin_number=int(header[1:]),
        sensor_active_level=sensor_active_level or sensor["pins"][signal.from_pin.pin]["active_level"],
        debounce_ms=debounce_ms or DEFAULT_DEBOUNCE_MS,
        led_symbol=led["symbol"], led_on_level=led["on_level"],
        log_object=logging_["object"], log_setup=logging_["setup"], allowed_includes=[logging_["include"]],
        required_events=REQUIRED_EVENTS, facts=[by_id[f] for f in quoted])


def task_prompt(ctx: CodegenContext) -> str:
    behavior = {
        Behavior.toggle_on_event: "Each new sound event flips the LED: off -> on -> off.",
        Behavior.on_while_active: "The LED is on while the sensor reports sound and off otherwise.",
    }[ctx.project.behavior]
    off = "HIGH" if ctx.led_on_level == "LOW" else "LOW"
    return f"""PROJECT: {ctx.project.title}. {ctx.project.summary}
BEHAVIOR: {behavior}

BOARD FACTS (verified; the only truth about this hardware):
{chr(10).join("- " + f for f in ctx.facts)}

APPROVED HARDWARE ACCESS (nothing else may be touched):
- Sensor signal: {ctx.sensor_name} digital output on header {ctx.sensor_pin}, Arduino pin number {ctx.sensor_pin_number}. pinMode INPUT, read with digitalRead.
- Output: {ctx.led_symbol}. pinMode OUTPUT. {ctx.led_on_level} turns it on, {off} turns it off. Start with it off.
- Approved #include: {", ".join("<" + h + ">" for h in ctx.allowed_includes)}
- Logging: {ctx.log_object}.println(...), started in setup() with: {" ".join(ctx.log_setup)}

REQUIRED PARAMETERS (declare each once near the top as a const, with exactly these values, and use them):
- const int SENSOR_ACTIVE_LEVEL = {ctx.sensor_active_level};   // level on the pin while the sensor reports sound
- const unsigned long DEBOUNCE_MS = {ctx.debounce_ms};   // after an event, ignore new events for this long

REQUIRED LOGIC:
- Edge detection: react when the pin changes from inactive to SENSOR_ACTIVE_LEVEL, not while it stays active.
- Debounce with millis() and DEBOUNCE_MS, without blocking.

REQUIRED LOG LINES (each exactly this text on its own line):
- READY            once, at the end of setup()
- SOUND_DETECTED   on each accepted sound event
- LED_ON / LED_OFF whenever the LED changes, after the change"""


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
              issues: List[CodeIssue], ctx: CodegenContext) -> GenerationAttempt:
        """Static checks, then save, then compile. A sketch that fails the static checks is never compiled."""
        attempt = GenerationAttempt(attempt=number, kind=kind, issues=list(issues))
        if sketch is None:
            return attempt
        attempt.issues += check_response(sketch, ctx)
        attempt.project_dir = str(save_project(session_id, number, sketch, ctx))
        if not attempt.issues:
            attempt.compile = self.compiler.compile(Path(attempt.project_dir), ctx.fqbn)
            if attempt.compile.status == CompileStatus.failed:
                attempt.issues.append(CodeIssue(check="compile", rule="compile_error",
                                                message=codecheck.compile_errors(attempt.compile.log)))
        return attempt

    def generate(self, session_id: str, ctx: CodegenContext) -> Tuple[GeneratedSketch, str, List[GenerationAttempt]]:
        """Returns (sketch, source, attempts) with source in llm | template | demo. Raises if even the template fails."""
        attempts: List[GenerationAttempt] = []
        if self.client is not None:
            prompt = task_prompt(ctx)
            for number in range(MAX_REPAIRS + 1):
                sketch, issues = self._ask(prompt)
                attempt = self._gate(session_id, number, "generate" if number == 0 else "repair", sketch, issues, ctx)
                attempts.append(attempt)
                if not attempt.issues:
                    return sketch, "llm", attempts
                if any(i.rule == "llm_error" for i in attempt.issues):
                    break  # the service is down: asking again will not help
                previous = sketch.source_code if sketch else "(no valid sketch was returned)"
                problems = "\n".join(i.message if i.check == "compile" else codecheck.summarize([i])
                                     for i in attempt.issues)
                prompt = repair_prompt(ctx, previous, problems)

        sketch = template_sketch(ctx)
        attempt = self._gate(session_id, len(attempts), "template", sketch, [], ctx)
        attempts.append(attempt)
        if attempt.issues:
            raise RuntimeError("the template sketch failed its own checks: " + codecheck.summarize(attempt.issues))
        return sketch, "demo" if self.mode == "demo" else "template", attempts


def final_compile(attempts: List[GenerationAttempt], fqbn: str) -> CompileReport:
    return attempts[-1].compile or CompileReport(status=CompileStatus.not_run, fqbn=fqbn)
