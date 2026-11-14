"""Sketch generation: prompt contract, response schema, static checks, compile feedback, repair limit."""
import io
import json
import os
import stat
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import codecheck, codegen, compiler, planner, session_router
from app.build.models import CompileReport, CompileStatus
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
import example_clap_session as example  # noqa: E402
import learnlens_agent  # noqa: E402

client = TestClient(app)
GOOD = example.CANNED_SKETCH
CODE = GOOD["source_code"]
INVENTORY = [{"component_id": "uno-q"}, {"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]


def ctx(**overrides):
    spec = planner.clap_light_spec()
    return codegen.build_context(spec, planner.plan_wiring(spec), **overrides)


def variant(old, new):
    assert old in CODE
    return dict(GOOD, source_code=CODE.replace(old, new))


def wired_session():
    sid = client.post("/sessions", json={"instruction": example.INSTRUCTION, "confirmed_inventory": INVENTORY}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
    for i in range(len(s["wiring_steps"])):
        s = client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i}).json()
    assert s["state"] == "wiring_complete"
    return sid


def generate(sid, expect=200, **body):
    r = client.post(f"/sessions/{sid}/generate", json=body or None)
    assert r.status_code == expect, r.text
    return r.json()


class FakeCompiler:
    def __init__(self, *reports):
        self.reports, self.compiled = list(reports), []

    def compile(self, sketch_dir, fqbn):
        self.compiled.append((sketch_dir / "learnlens_sketch.ino").read_text())
        status, log = self.reports.pop(0) if self.reports else ("ok", "")
        return CompileReport(status=status, fqbn=fqbn, log=log)


@pytest.fixture(autouse=True)
def template_generator():
    session_router.generator = codegen.CodeGenerator(mode="mock")


# ---------- request to the model ----------

def test_prompt_is_built_from_the_plan_and_hardware_database():
    fake = example.use_fake_codegen(GOOD)
    generate(wired_session())
    req = fake.requests[0]
    task = req["messages"][0]["content"][0]["text"]
    for needed in ("header D2, Arduino pin number 2", "LED_BUILTIN", "LOW turns it on", "<Arduino_RouterBridge.h>",
                   "Monitor.begin();", "SENSOR_ACTIVE_LEVEL = HIGH", "DEBOUNCE_MS = 300", "arduino:zephyr:unoq",
                   "active-low", "READY", "SOUND_DETECTED", "LED_ON / LED_OFF"):
        assert needed in task, needed
    assert "not a classic Arduino UNO" in req["system"][0]["text"]
    schema = req["output_config"]["format"]["schema"]
    assert schema["required"] == ["source_code", "explanation", "display_summary", "assumptions",
                                  "expected_serial_events"]
    assert schema["additionalProperties"] is False


# ---------- happy path ----------

def test_generated_sketch_is_checked_saved_and_waits_for_approval():
    example.use_fake_codegen(GOOD)
    sid = wired_session()
    g = generate(sid)
    assert g["source"] == "llm" and g["repairs_used"] == 0 and [a["kind"] for a in g["attempts"]] == ["generate"]
    assert g["sketch"]["code"] == CODE and g["sketch"]["provenance"] == "llm"
    assert [e["event"] for e in g["expected_serial_events"]] == ["READY", "SOUND_DETECTED", "LED_ON", "LED_OFF"]
    assert g["explanation"] and g["assumptions"]
    # The canned answer has no display_summary: the first two sentences of the explanation are used.
    assert g["display_summary"] == ("The sketch watches the sound sensor on pin D2. When the signal switches to its "
                                    "active level, it flips the built-in LED and prints what happened.")
    long = codegen.GeneratedSketch.model_validate(dict(GOOD, display_summary="word " * 100))
    assert len(codegen.display_summary(long)) <= 180 and codegen.display_summary(long).endswith("…")
    assert g["parameters"] == {"SENSOR_ACTIVE_LEVEL": "HIGH", "DEBOUNCE_MS": 300, "sensor_pin": "D2",
                               "led": "LED_BUILTIN", "led_on_level": "LOW", "fqbn": "arduino:zephyr:unoq"}
    assert g["flashed"] is False and g["hardware_verified"] is False
    assert g["compile"]["status"] == "not_run"                       # no arduino-cli in unit tests

    project = Path(g["project_dir"])
    assert project.name == "learnlens_sketch" and project.parent.parent.name == sid
    assert (project / "learnlens_sketch.ino").read_text() == CODE
    assert json.loads((project.parent / "generation.json").read_text())["response"]["source_code"] == CODE

    status = client.get(f"/sessions/{sid}/status").json()
    assert status["state"] == "code_generated" and status["build_id"] == g["build_id"]
    build = client.get(f"/builds/{g['build_id']}").json()
    assert build["status"] == "awaiting_approval" and build["deploy"] is None
    assert build["expected_serial_events"] == ["READY", "SOUND_DETECTED", "LED_ON", "LED_OFF"]
    # Nothing can be flashed: the deploy agent finds no job until the user approves.
    assert learnlens_agent.run_once(lambda p, b: (client.post(p, json=b).status_code, None), learnlens_agent.mock_agent()) is None


def test_template_is_used_without_ai_and_honours_polarity_and_debounce():
    g = generate(wired_session(), sensor_active_level="LOW", debounce_ms=150)
    code = g["sketch"]["code"]
    assert g["source"] == "template" and g["sketch"]["provenance"] == "rules"
    assert "const int SENSOR_ACTIVE_LEVEL = LOW;" in code and "const unsigned long DEBOUNCE_MS = 150;" in code
    assert "active && !wasActive" in code and "now - lastEventMs >= DEBOUNCE_MS" in code   # edge + debounce
    assert g["parameters"]["SENSOR_ACTIVE_LEVEL"] == "LOW" and g["parameters"]["DEBOUNCE_MS"] == 150
    assert client.post(f"/sessions/{wired_session()}/generate", json={"sensor_active_level": "MAYBE"}).status_code == 422
    assert client.post(f"/sessions/{wired_session()}/generate", json={"debounce_ms": 5}).status_code == 422


# ---------- static checks ----------

def test_good_sketches_pass():
    assert codecheck.check_sketch(CODE, ctx()) == []
    assert codecheck.check_sketch(codegen.template_sketch(ctx()).source_code, ctx()) == []
    harmless = CODE + '\n// while (true) { Serial.begin(9600); analogRead(A0); }\n/* #define X 1 */\n'
    harmless = harmless.replace('Monitor.println("READY");', 'Monitor.println("READY");\n  Monitor.println("for while Serial");')
    assert codecheck.check_sketch(harmless, ctx()) == []            # comments and strings are not code


@pytest.mark.parametrize("old,new,rule", [
    ("const int SENSOR_PIN = 2;", "const int SENSOR_PIN = 3;", "disallowed_pin"),
    ("digitalWrite(LED_PIN, HIGH);  // off", "digitalWrite(13, HIGH);", "disallowed_pin"),
    ("digitalWrite(LED_PIN, HIGH);  // off", "int p = 5; digitalWrite(p, HIGH);", "disallowed_pin"),
    ("digitalWrite(LED_PIN, HIGH);  // off", "digitalWrite(LED_PIN + 1, HIGH);", "disallowed_pin"),
    ("digitalWrite(LED_PIN, HIGH);  // off", "pinMode(A0, INPUT);", "disallowed_pin"),
    ("Monitor.begin();", "Monitor.begin(); Serial.begin(9600);", "disallowed_pin"),      # UART on D0/D1
    ("pinMode(SENSOR_PIN, INPUT);", "pinMode(SENSOR_PIN, OUTPUT);", "unsafe_operation"),
    ("pinMode(SENSOR_PIN, INPUT);", "pinMode(SENSOR_PIN, INPUT); digitalWrite(SENSOR_PIN, HIGH);", "unsafe_operation"),
    ("#include <Arduino_RouterBridge.h>", "#include <Arduino_RouterBridge.h>\n#include <Servo.h>", "unexpected_library"),
    ("#include <Arduino_RouterBridge.h>", '#include "../secrets.h"', "unexpected_library"),
    ("const int SENSOR_PIN = 2;", "#define SENSOR_PIN 2", "preprocessor"),
    ("lastLevel = level;", "lastLevel = level; while (true) {}", "unsafe_loop"),
    ("lastLevel = level;", "lastLevel = level; delay(1000);", "blocking_delay"),
    ("lastLevel = level;", "lastLevel = level; attachInterrupt(2, loop, RISING);", "interrupts"),
    ("lastLevel = level;", 'lastLevel = level; Bridge.call("shell", "rm -rf /");', "bridge_rpc"),
    ("lastLevel = level;", "lastLevel = level; analogWrite(LED_PIN, 128);", "analog_io"),
    ("lastLevel = level;", "lastLevel = level; GPIOB->ODR = 1;", "raw_register"),
    ("lastLevel = level;", "lastLevel = level; *(volatile unsigned*)0x42020414 = 1;", "raw_register"),
    ("lastLevel = level;", "lastLevel = level; gpio_pin_set(0, 3, 1);", "zephyr_api"),
    ("lastLevel = level;", "lastLevel = level; int* p = new int[4];", "dynamic_memory"),
    ("lastLevel = level;", "lastLevel = level; Wire.begin();", "other_bus"),
    ('Monitor.println("READY");', "", "missing_event"),
    ("const int SENSOR_ACTIVE_LEVEL = HIGH;", "const int SENSOR_ACTIVE_LEVEL = LOW;", "wrong_parameter"),
    ("const unsigned long DEBOUNCE_MS = 300;", "const unsigned long DEBOUNCE_MS = 5;", "wrong_parameter"),
    ("const unsigned long DEBOUNCE_MS = 300;", "unsigned long DEBOUNCE_MS = 300;", "missing_parameter"),
    ("millis()", "0", "missing_debounce"),
    ("Monitor.begin();", "", "missing_logging"),
    ("void loop() {", "void run() {", "missing_function"),
])
def test_static_checks_reject(old, new, rule):
    issues = codecheck.check_sketch(variant(old, new)["source_code"], ctx())
    assert rule in {i.rule for i in issues}, [f"{i.rule}: {i.message}" for i in issues]


def test_static_failure_is_never_compiled():
    fake_compiler = FakeCompiler()
    example.use_fake_codegen(variant("const int SENSOR_PIN = 2;", "const int SENSOR_PIN = 3;"), GOOD, compiler=fake_compiler)
    g = generate(wired_session())
    assert fake_compiler.compiled == [CODE]                          # only the sketch that passed the checks
    assert g["attempts"][0]["compile"] is None


# ---------- repair loop ----------

def test_static_problem_is_sent_back_and_repaired():
    bad = variant("const int SENSOR_PIN = 2;", "const int SENSOR_PIN = 3;")
    fake = example.use_fake_codegen(bad, GOOD)
    g = generate(wired_session())
    assert g["source"] == "llm" and g["repairs_used"] == 1
    assert [a["kind"] for a in g["attempts"]] == ["generate", "repair"]
    assert g["attempts"][0]["issues"][0]["rule"] == "disallowed_pin" and g["attempts"][1]["issues"] == []
    repair = fake.requests[1]["messages"][0]["content"][0]["text"]
    assert "REJECTED" in repair and "not in the confirmed wiring plan" in repair and "SENSOR_PIN = 3" in repair
    assert "header D2" in repair                                      # the full task is restated


def test_compile_error_is_sent_back_and_repaired():
    log = "learnlens_sketch.ino:21:3: error: 'Montior' was not declared in this scope\nexit status 1"
    fake_compiler = FakeCompiler(("failed", log), ("ok", "Sketch uses 87032 bytes"))
    fake = example.use_fake_codegen(GOOD, GOOD, compiler=fake_compiler)
    g = generate(wired_session())
    assert g["repairs_used"] == 1 and g["compile"]["status"] == "ok" and g["source"] == "llm"
    assert g["attempts"][0]["issues"][0]["check"] == "compile"
    assert "'Montior' was not declared" in fake.requests[1]["messages"][0]["content"][0]["text"]


def test_at_most_two_repairs_then_the_template_takes_over():
    bad = variant("lastLevel = level;", "lastLevel = level; while (true) {}")
    fake = example.use_fake_codegen(bad, bad, bad, GOOD)
    g = generate(wired_session())
    assert len(fake.requests) == 3                                    # 1 generation + 2 repairs, never a 4th call
    assert [a["kind"] for a in g["attempts"]] == ["generate", "repair", "repair", "template"]
    assert g["source"] == "template" and g["repairs_used"] == 2 and g["sketch"]["provenance"] == "rules"
    assert "while" not in codecheck.blank(g["sketch"]["code"], strings=True)


@pytest.mark.parametrize("first,calls,kinds", [
    ("not json", 2, ["generate", "repair"]),                          # unreadable answer: ask again
    ({"source_code": CODE}, 2, ["generate", "repair"]),               # schema violation: ask again
    (dict(GOOD, expected_serial_events=[]), 2, ["generate", "repair"]),
    (RuntimeError("api down"), 1, ["generate", "template"]),          # service down: do not keep calling
])
def test_invalid_answers_and_outages(first, calls, kinds):
    fake = example.use_fake_codegen(first, GOOD)
    g = generate(wired_session())
    assert len(fake.requests) == calls and [a["kind"] for a in g["attempts"]] == kinds
    assert g["attempts"][0]["issues"][0]["check"] == "schema"


# ---------- state machine ----------

def test_generate_only_after_wiring_and_regeneration_replaces_the_sketch():
    sid = client.post("/sessions", json={"instruction": example.INSTRUCTION, "confirmed_inventory": INVENTORY}).json()["session_id"]
    generate(sid, expect=409)                                         # nothing analyzed yet
    client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"})
    generate(sid, expect=409)                                         # wiring not confirmed yet

    sid = wired_session()
    first = generate(sid)
    second = generate(sid, sensor_active_level="LOW")                 # user flips the polarity
    assert second["build_id"] != first["build_id"] and "SENSOR_ACTIVE_LEVEL = LOW" in second["sketch"]["code"]
    assert client.get(f"/builds/{first['build_id']}").json()["status"] == "rejected"
    client.post(f"/builds/{second['build_id']}/approve", json={"sketch_sha256": second["sketch"]["sha256"]})
    generate(sid, expect=409)                                         # approved sketches are not replaced


# ---------- compile step ----------

def test_compile_command_can_only_compile():
    command = compiler.compile_command("arduino-cli", "arduino:zephyr:unoq", Path("/tmp/x/learnlens_sketch"))
    assert command == ["arduino-cli", "compile", "--fqbn", "arduino:zephyr:unoq", "--warnings", "default",
                       "--no-color", "/tmp/x/learnlens_sketch"]
    assert not compiler.FORBIDDEN_ARGS & set(command)
    assert "upload" not in Path(compiler.__file__).read_text().replace("never upload", "").replace(
        "no upload path", "").replace('"upload", "-u", "--upload"', "").replace("it never uploads", "")


@pytest.mark.parametrize("script,expected", [
    ("echo 'Sketch uses 87032 bytes'; exit 0", CompileStatus.ok),
    ("echo \"sketch.ino:3:1: error: 'x' was not declared\" >&2; exit 1", CompileStatus.failed),
    ("echo 'Error during build: Invalid FQBN: board arduino:zephyr:unoq not found' >&2; exit 1", CompileStatus.not_run),
    ("echo 'Error: platform arduino:zephyr is not installed' >&2; exit 1", CompileStatus.not_run),
])
def test_compiler_tells_sketch_errors_from_setup_problems(tmp_path, monkeypatch, script, expected):
    cli = tmp_path / "fake-arduino-cli"
    cli.write_text(f"#!/bin/sh\n{script}\n")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("LEARNLENS_ARDUINO_CLI", str(cli))
    report = compiler.ArduinoCliCompiler().compile(tmp_path, "arduino:zephyr:unoq")
    assert report.status == expected and report.command[1] == "compile"


def test_no_cli_means_not_run_and_bad_session_ids_never_reach_the_disk():
    report = compiler.ArduinoCliCompiler().compile(Path("/nonexistent"), "arduino:zephyr:unoq")
    assert report.status == CompileStatus.not_run and "not found" in report.detail
    with pytest.raises(ValueError):
        codegen.save_project("../../etc", 0, codegen.template_sketch(ctx()), ctx())


@pytest.mark.skipif(os.getenv("LEARNLENS_REAL_COMPILE") != "1", reason="set LEARNLENS_REAL_COMPILE=1 (needs arduino-cli + arduino:zephyr)")
@pytest.mark.parametrize("name", ["template", "canned"])
def test_sketches_compile_with_the_real_uno_q_core(name, tmp_path, monkeypatch):
    monkeypatch.delenv("LEARNLENS_ARDUINO_CLI")
    monkeypatch.setattr(codegen, "PROJECTS_DIR", tmp_path)
    sketch = codegen.template_sketch(ctx()) if name == "template" else codegen.GeneratedSketch.model_validate(GOOD)
    report = compiler.ArduinoCliCompiler().compile(codegen.save_project("abc123", 0, sketch, ctx()), ctx().fqbn)
    assert report.status == CompileStatus.ok, report.log[-1500:]
