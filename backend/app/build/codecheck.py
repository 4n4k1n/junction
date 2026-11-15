"""Static checks on a generated sketch, before it is saved, compiled or shown for approval.

The sketch may only touch the pins of the confirmed wiring plan, include the approved headers,
and use a small polling-style subset of the Arduino API. Anything the checker cannot resolve to
an approved pin counts as a violation: it does not try to be clever about what code might mean.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .models import CodegenContext, CodeIssue

MAX_SOURCE_CHARS = 8000
MAX_DELAY_MS = 50

# (rule, pattern, message). Matched against the code with comments and string literals blanked.
DENYLIST = [
    ("unsafe_loop", r"\b(do|goto)\b", "do-while and goto are not allowed"),
    ("dynamic_memory", r"\b(malloc|calloc|realloc|free|new|delete)\b", "dynamic memory is not allowed"),
    ("inline_asm", r"\b(asm|__asm__?|__attribute__)\b", "inline assembly and attributes are not allowed"),
    ("raw_register", r"\b(GPIO[A-K]|RCC|SCB|NVIC|EXTI|TIM\d+|ADC\d+|DAC\d*|PWR|FLASH)\b|\bHAL_\w+|\bLL_\w+|\b__HAL\w+",
     "direct STM32 register or HAL access is not allowed"),
    ("raw_register", r"\(\s*(volatile\s+)?\w+\s*\*\s*\)\s*0x[0-9a-fA-F]+|\bvolatile\b|\breinterpret_cast\b",
     "casting addresses to pointers is not allowed"),
    ("zephyr_api", r"\b(k_\w+|z_\w+|device_get_binding|gpio_pin_\w+|sys_reboot|irq_lock)\b",
     "Zephyr kernel and driver calls are not allowed: use the Arduino API"),
    ("interrupts", r"\b(attachInterrupt|detachInterrupt|noInterrupts|interrupts|__disable_irq|__enable_irq)\b",
     "interrupt control is not allowed: poll the pin in loop()"),
    ("analog_io", r"\b(analogWrite|analogReference|analogReadResolution|tone|noTone|pulseIn|shiftOut|shiftIn)\b",
     "analog and pulse output are not allowed"),
    ("bridge_rpc", r"\bBridge\s*\.\s*(?!begin\b)\w+", "RPC calls to the board's Linux side are not allowed"),
    ("other_bus", r"\b(Serial\d+|Wire\d*|SPI\d*|CAN\d*|EEPROM|WiFi\w*|BLE\w*|SD|Servo|Keyboard|Mouse)\b",
     "buses and peripherals outside the wiring plan are not allowed"),
    ("process_or_file", r"\b(system|popen|exec\w*|fork|fopen|fwrite|remove|exit|abort|reboot|NVIC_SystemReset)\s*\(",
     "process, file and reset calls are not allowed"),
]

CONST_DECL = re.compile(
    r"\b(?:static\s+)?(?:const|constexpr)\s+(?:static\s+)?(?:unsigned\s+)?(?:int|long|bool|byte|uint8_t|uint16_t|uint32_t|"
    r"pin_size_t|PinStatus)(?:\s+long)?\s+(\w+)\s*=\s*([^;]+);")
PIN_CALL = re.compile(r"\b(pinMode|digitalRead|digitalWrite|analogRead)\s*\(\s*([^,()]*)\s*(?:,\s*([^()]*?)\s*)?\)")
IDENT = re.compile(r"^[A-Za-z_]\w*$")


def blank(code: str, strings: bool) -> str:
    """Replace comments (and string/char literals when `strings`) with spaces, keeping line numbers."""
    out, i, n = [], 0, len(code)
    while i < n:
        two = code[i:i + 2]
        if two == "//":
            j = code.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
        elif two == "/*":
            j = code.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append(re.sub(r"[^\n]", " ", code[i:j]))
            i = j
        elif code[i] in "\"'":
            quote, j = code[i], i + 1
            while j < n and code[j] != quote and code[j] != "\n":
                j += 2 if code[j] == "\\" else 1
            j = min(j + 1, n)
            out.append(quote + " " * (j - i - 2) + quote if strings and j - i >= 2 else code[i:j])
            i = j
        else:
            out.append(code[i])
            i += 1
    return "".join(out)


def string_literals(code: str) -> List[str]:
    return re.findall(r'"((?:[^"\\\n]|\\.)*)"', blank(code, strings=False))


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _resolve(token: str, constants: Dict[str, str]) -> str:
    for _ in range(4):
        if token not in constants:
            break
        token = constants[token]
    return token


def check_sketch(code: str, ctx: CodegenContext) -> List[CodeIssue]:
    issues: List[CodeIssue] = []

    def add(rule: str, message: str, pos: Optional[int] = None, text: str = "") -> None:
        issues.append(CodeIssue(check="static", rule=rule, message=message,
                                line=_line(text, pos) if pos is not None else None))

    if len(code) > MAX_SOURCE_CHARS:
        add("too_long", f"sketch is {len(code)} characters, limit is {MAX_SOURCE_CHARS}")
    no_comments = blank(code, strings=False)
    bare = blank(code, strings=True)
    if not bare.isascii():
        add("non_ascii", "non-ASCII characters outside comments and strings")

    # Preprocessor: only #include of approved headers. No macros, so nothing can be renamed or hidden.
    for m in re.finditer(r"^[ \t]*#[ \t]*(\w*)(.*)$", no_comments, re.M):
        directive, rest = m.group(1), m.group(2).strip()
        if directive != "include":
            add("preprocessor", f"#{directive} is not allowed: use const values", m.start(), no_comments)
            continue
        header = re.fullmatch(r'[<"]([\w./-]+)[>"]', rest)
        if not header or header.group(1) not in ctx.allowed_includes:
            add("unexpected_library", f"#include {rest} is not an approved library "
                                      f"(approved: {', '.join(ctx.allowed_includes) or 'none'})", m.start(), no_comments)

    for rule, pattern, message in DENYLIST:
        for m in re.finditer(pattern, bare):
            add(rule, f"{m.group(0).strip()}: {message}", m.start(), bare)
    custom = bool(ctx.custom_behavior)
    analog_in = {i.header for i in ctx.inputs if i.kind == "analog"}
    digital_in = {name for i in ctx.inputs if i.kind == "digital" for name in (str(i.number), i.header)}
    analog = bool(analog_in)
    if not (analog or custom):
        # A digital project polls in loop(): no loops that could block, and no analog input.
        for m in re.finditer(r"\b(while|for)\b", bare):
            add("unsafe_loop", f"{m.group(0)}: loops are not allowed here: loop() must return quickly", m.start(), bare)
    if not analog:
        for m in re.finditer(r"\banalogRead\b", bare):
            add("analog_io", "analogRead: this project uses digital I/O only", m.start(), bare)

    if ctx.log_object != "Serial":
        for m in re.finditer(r"\bSerial\b", bare):
            add("disallowed_pin", "Serial is the hardware UART on pins D0/D1 on the UNO Q, which are not in the "
                                  f"wiring plan: log with {ctx.log_object}", m.start(), bare)

    for m in re.finditer(r"\bdelay(?:Microseconds)?\s*\(\s*([^()]*)\)", bare):
        arg = m.group(1).strip()
        if not arg.isdigit() or (int(arg) > MAX_DELAY_MS and "Micro" not in m.group(0)):
            add("blocking_delay", f"delay({arg}) blocks sound detection: at most delay({MAX_DELAY_MS})", m.start(), bare)

    # Pins: every pin argument must resolve, through const declarations, to an approved pin.
    constants = {name: value.strip() for name, value in CONST_DECL.findall(bare)}
    led = {ctx.led_symbol} if (ctx.output == "builtin_led" or custom) else set()
    approved = ", ".join([f"{i.header} as input" for i in ctx.inputs] + [f"{name} as output" for name in led])
    read_pins = set()
    for m in PIN_CALL.finditer(bare):
        call, arg, second = m.group(1), m.group(2).strip(), (m.group(3) or "").strip()
        if not (IDENT.match(arg) or arg.isdigit()):
            add("disallowed_pin", f"{call}({arg}): the pin must be a named constant", m.start(), bare)
            continue
        pin = _resolve(arg, constants)
        if pin in analog_in:
            if call != "analogRead":
                add("disallowed_pin", f"{call}({arg}): read the analog sensor pin with analogRead only", m.start(), bare)
            else:
                read_pins.add(pin)
        elif pin in digital_in:
            if call == "analogRead":
                add("disallowed_pin", f"analogRead({arg}): {pin} is a digital input", m.start(), bare)
            elif call == "digitalWrite":
                add("unsafe_operation", f"digitalWrite on {arg}: the sensor output must never be driven", m.start(), bare)
            elif call == "pinMode" and _resolve(second, constants) != "INPUT":
                add("unsafe_operation", f"pinMode({arg}, {second}): the sensor pin must be INPUT", m.start(), bare)
            elif call == "digitalRead":
                read_pins.add(pin)
        elif pin in led:
            if call == "pinMode" and _resolve(second, constants) != "OUTPUT":
                add("unsafe_operation", f"pinMode({arg}, {second}): the LED pin must be OUTPUT", m.start(), bare)
        else:
            add("disallowed_pin", f"{call}({arg}) uses pin '{pin}', which is not in the confirmed wiring plan "
                                  f"(approved: {approved})", m.start(), bare)

    # Contract: structure, configurable parameters, logging.
    for name in ("setup", "loop"):
        if not re.search(rf"\bvoid\s+{name}\s*\(\s*(void)?\s*\)", bare):
            add("missing_function", f"void {name}() is missing")
    for i in ctx.inputs:
        if not ({i.header, str(i.number)} & read_pins):
            add("missing_input", f"the sensor on {i.header} is never read with "
                                 f"{'analogRead' if i.kind == 'analog' else 'digitalRead'}")
    if ctx.output == "led_matrix" and not custom and not re.search(rf"\b{ctx.matrix_type}\s+\w+", bare):
        add("missing_output", f"no {ctx.matrix_type} object is declared")
    expected = ({} if custom else {"SAMPLE_WINDOW_MS": str(ctx.sample_window_ms)} if analog
                else {"SENSOR_ACTIVE_LEVEL": ctx.sensor_active_level, "DEBOUNCE_MS": str(ctx.debounce_ms)})
    for name, value in expected.items():
        got = re.sub(r"(?<=\d)[uUlL]+$", "", constants.get(name, ""))
        if not got:
            add("missing_parameter", f"const {name} is missing: it must be declared once near the top")
        elif got != value:
            add("wrong_parameter", f"{name} is {got}, the confirmed value is {value}")
        elif len(re.findall(rf"\b{name}\b", bare)) < 2:
            add("unused_parameter", f"{name} is declared but never used")
    if not custom and not re.search(r"\bmillis\s*\(\s*\)", bare):
        add("missing_debounce", "millis() is never used, so nothing is timed")
    if not re.search(rf"\b{ctx.log_object}\s*\.\s*begin\s*\(", bare):
        add("missing_logging", f"{ctx.log_object}.begin(...) is missing")
    literals = string_literals(code)
    for event in ctx.required_events:
        if not any(event in s for s in literals):
            add("missing_event", f'the log line "{event}" is missing')
    return issues


def summarize(issues: List[CodeIssue]) -> str:
    return "\n".join(f"- [{i.rule}]" + (f" line {i.line}:" if i.line else "") + f" {i.message}" for i in issues)


def compile_errors(log: str, limit: int = 2500) -> str:
    """The lines of a compiler log worth sending back for a repair."""
    lines = [l for l in log.splitlines() if re.search(r"\b(error|undefined reference|was not declared)\b", l)]
    return "\n".join(lines or log.splitlines()[-25:])[:limit]
