"""Second project: a sound level meter on the LED matrix, driven by the sensor's analog output."""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import codecheck, codegen, compiler, facts, planner, session_router
from app.build.analyzer import Analyzer, analysis_schema, keyword_project
from app.build.models import Behavior, CompileStatus
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import example_clap_session as example  # noqa: E402

client = TestClient(app)
INVENTORY = [{"component_id": "uno-q"}, {"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]
GOAL = "Build a sound level meter: the louder it is, the more LEDs light up"


def ctx(**overrides):
    spec = planner.level_meter_spec()
    return codegen.build_context(spec, planner.plan_wiring(spec), **overrides)


CODE = codegen.template_sketch(ctx()).source_code


@pytest.fixture(autouse=True)
def offline():
    session_router.analyzer = Analyzer(mode="mock")
    session_router.generator = codegen.CodeGenerator(mode="mock")


def wired(goal=GOAL):
    sid = client.post("/sessions", json={"instruction": goal, "confirmed_inventory": INVENTORY}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
    assert s["state"] == "wiring", s
    steps = s["wiring_steps"]
    for i in range(len(steps)):
        client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    return sid, s


def test_level_meter_flow_wires_the_analog_output_and_draws_on_the_matrix():
    sid, s = wired()
    assert s["project"]["behavior"] == "level_meter" and s["project"]["output"] == "led_matrix"
    links = [(c["from_pin"]["pin"], c["to_pin"]["pin"]) for c in (st["connection"] for st in s["wiring_steps"]) if c]
    assert links == [("G", "GND"), ("+", "3V3"), ("AO", "A0")]                 # analog out, never on 5 V
    assert "unoq-analog-limit" in s["wiring_steps"][2]["fact_ids"]

    g = client.post(f"/sessions/{sid}/generate", json={"sample_window_ms": 40}).json()
    code = g["sketch"]["code"]
    assert "#include <Arduino_LED_Matrix.h>" in code and "analogRead(MIC_PIN)" in code and "const int MIC_PIN = A0;" in code
    assert "const unsigned long SAMPLE_WINDOW_MS = 40;" in code and "uint8_t frame[104];" in code
    assert g["parameters"] == {"SAMPLE_WINDOW_MS": 40, "sensor_pin": "A0", "output": "led_matrix", "fqbn": "arduino:zephyr:unoq"}
    assert [e["event"] for e in g["expected_serial_events"]] == ["READY", "LOUD", "QUIET"]
    assert code.count('Monitor.println("LOUD")') == 1 and "wasLoud" in code
    assert g["flashed"] is False and g["hardware_verified"] is False
    assert client.get(f"/builds/{g['build_id']}").json()["status"] == "awaiting_approval"


def test_the_clap_light_is_unchanged_and_keywords_pick_the_project():
    _, s = wired("I want to build a clap-activated light.")
    assert s["project"]["behavior"] == "toggle_on_event" and s["project"]["output"] == "builtin_led"
    assert keyword_project("Lautstärke mit vielen LEDs anzeigen").behavior == Behavior.level_meter
    for phrase in ("more LEDs light up when the sound gets higher", "viele LEDs über AOUT ansteuern",
                   "show the sound on the led matrix", "Bei höherer Lautstärke mehr LEDs"):
        assert keyword_project(phrase).behavior == Behavior.level_meter, phrase
    assert keyword_project("toggle the light when I clap").behavior == Behavior.toggle_on_event
    assert keyword_project("blink a servo").behavior == Behavior.unsupported
    assert "level_meter" in analysis_schema()["properties"]["project"]["properties"]["behavior"]["enum"]


def test_prompt_for_the_level_meter_comes_from_the_hardware_database():
    fake = example.use_fake_codegen({"source_code": CODE, "explanation": "x", "assumptions": [],
                                     "expected_serial_events": [{"event": e, "when": "x"} for e in ("READY", "LOUD", "QUIET")]})
    sid, _ = wired()
    g = client.post(f"/sessions/{sid}/generate").json()
    assert g["source"] == "llm" and g["repairs_used"] == 0
    task = fake.requests[0]["messages"][0]["content"][0]["text"]
    for needed in ("analogRead(A0)", "8 rows by 13 columns", "Arduino_LED_Matrix", "<Arduino_LED_Matrix.h>",
                   "SAMPLE_WINDOW_MS = 25", "not 5 V-tolerant", "matrix.draw(frame)", "for and while loops are allowed"):
        assert needed in task, needed
    assert "SENSOR_ACTIVE_LEVEL" not in task and "LED_BUILTIN" not in task


def test_template_passes_its_checks():
    assert codecheck.check_sketch(CODE, ctx()) == []


@pytest.mark.parametrize("old,new,rule", [
    ("const int MIC_PIN = A0;", "const int MIC_PIN = A1;", "disallowed_pin"),
    ("matrix.clear();", "matrix.clear(); pinMode(LED_BUILTIN, OUTPUT); digitalWrite(LED_BUILTIN, LOW);", "disallowed_pin"),
    ("matrix.clear();", "matrix.clear(); pinMode(MIC_PIN, OUTPUT);", "disallowed_pin"),
    ("matrix.clear();", "matrix.clear(); analogWrite(A0, 200);", "analog_io"),
    ("matrix.clear();", "matrix.clear(); Serial.begin(9600);", "disallowed_pin"),
    ("matrix.clear();", "matrix.clear(); do { } while (true);", "unsafe_loop"),
    ("#include <Arduino_LED_Matrix.h>", "#include <Arduino_LED_Matrix.h>\n#include <Servo.h>", "unexpected_library"),
    ("const unsigned long SAMPLE_WINDOW_MS = 25;", "const unsigned long SAMPLE_WINDOW_MS = 90;", "wrong_parameter"),
    ("Arduino_LED_Matrix matrix;", "", "missing_output"),
    ("analogRead(MIC_PIN)", "512", "missing_input"),
    ('Monitor.println("READY");', "", "missing_event"),
])
def test_static_checks_for_the_level_meter(old, new, rule):
    assert old in CODE
    issues = codecheck.check_sketch(CODE.replace(old, new), ctx())
    assert rule in {i.rule for i in issues}, [f"{i.rule}: {i.message}" for i in issues]


def test_analog_output_is_only_safe_on_the_3v3_rail():
    steps = planner.plan_wiring(planner.level_meter_spec())
    assert facts.check_safety(steps) == []
    steps[1].connection.to_pin.pin = "5V"
    assert any("AO can reach 5.5 V but uno-q A0 tolerates 3.6 V" in p for p in facts.check_safety(steps))


@pytest.mark.skipif(os.getenv("LEARNLENS_REAL_COMPILE") != "1", reason="set LEARNLENS_REAL_COMPILE=1")
def test_level_meter_compiles_with_the_real_uno_q_core(tmp_path, monkeypatch):
    monkeypatch.delenv("LEARNLENS_ARDUINO_CLI")
    monkeypatch.setattr(codegen, "PROJECTS_DIR", tmp_path)
    report = compiler.ArduinoCliCompiler().compile(
        codegen.save_project("abc123", 0, codegen.template_sketch(ctx()), ctx()), ctx().fqbn)
    assert report.status == CompileStatus.ok, report.log[-1500:]


def test_only_the_level_meter_is_offered_by_default(monkeypatch):
    monkeypatch.setenv("LEARNLENS_OFFERED_PROJECTS", "level_meter")
    from app.build.analyzer import analyze_system, to_spec

    assert analysis_schema()["properties"]["project"]["properties"]["behavior"]["enum"] == ["level_meter", "unsupported"]
    assert "toggle_on_event" not in analyze_system() and "level_meter" in analyze_system()
    clap = to_spec(keyword_project("I want to build a clap-activated light"))
    assert clap.supported is False and "sound level meter" in clap.unsupported_reason
    assert to_spec(keyword_project(GOAL)).supported is True

    sid = client.post("/sessions", json={"instruction": "clap light", "confirmed_inventory": INVENTORY}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
    assert s["state"] == "unsupported" and "sound level meter" in s["spoken_text"]
