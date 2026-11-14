"""Developing a finished build further: add a known part, or change what it does."""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import codecheck, codegen, compiler, facts, planner, session_router
from app.build.analyzer import Analyzer
from app.build.models import Behavior, CompileStatus
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
import example_clap_session as example  # noqa: E402
import learnlens_agent  # noqa: E402

client = TestClient(app)
INVENTORY = [{"component_id": "uno-q"}, {"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]
ADD_IR = "Add the infrared sensor so it only shows the level when my hand is near"


@pytest.fixture(autouse=True)
def offline():
    session_router.analyzer = Analyzer(mode="mock")
    session_router.generator = codegen.CodeGenerator(mode="mock")


def agent_post(path, payload):
    r = client.post(path, json=payload)
    return r.status_code, (r.json() if r.content else None)


def generated_level_meter():
    sid = client.post("/sessions", json={"instruction": "Build a sound level meter",
                                         "confirmed_inventory": INVENTORY}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
    for i in range(len(s["wiring_steps"])):
        client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    return sid, client.post(f"/sessions/{sid}/generate").json()


def finished_level_meter():
    sid, g = generated_level_meter()
    client.post(f"/builds/{g['build_id']}/approve", json={"sketch_sha256": g["sketch"]["sha256"]})
    for _ in range(30):
        if client.get(f"/builds/{g['build_id']}").json()["status"] != "approved":
            break
        learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent())
    assert client.post(f"/builds/{g['build_id']}/test-result", json={"passed": True}).json()["status"] == "done"
    return sid, g


def extend(sid, text, expect=200):
    r = client.post(f"/sessions/{sid}/extend", json={"instruction": text})
    assert r.status_code == expect, r.text
    return r.json()


def gated_ctx():
    spec = planner.level_meter_spec()
    steps = planner.plan_wiring(spec)
    steps += planner.plan_extension(steps, "ky-032")
    spec.behavior, spec.custom_behavior, spec.requests = Behavior.custom, "gate the meter", [ADD_IR]
    spec.components.append("ky-032")
    return codegen.build_context(spec, steps)


GATED = codegen.template_sketch(gated_ctx()).source_code


def test_adding_a_part_keeps_the_old_wiring_and_asks_only_for_the_new_steps():
    sid, first = finished_level_meter()
    s = extend(sid, ADD_IR)
    assert s["state"] == "needs_confirmation" and s["build_id"] is None
    assert s["project"]["behavior"] == "custom" and s["project"]["components"] == ["uno-q", "ky-037", "ky-032"]
    assert [p["id"] for p in s["pending_confirmations"]] == ["ky-032-present", "ky032-pin-labels"]
    assert len(s["wiring_steps"]) == 10 and s["confirmed_steps"] == [0, 1, 2, 3, 4]

    confirmed = '[{"component_id": "ky-032", "confirmations": ["ky032-pin-labels"]}]'
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": confirmed}).json()
    assert s["state"] == "wiring" and s["current_step"] == 5                     # the first new step
    new = [(c["from_pin"]["pin"], c["to_pin"]["pin"]) for c in (st["connection"] for st in s["wiring_steps"][5:]) if c]
    assert new == [("GND", "GND"), ("+", "3V3"), ("OUT", "D2")]                  # 3.3 V again, a free digital pin
    assert "only one 3.3 volt pin" in s["wiring_steps"][7]["spoken_text"]
    assert client.post(f"/sessions/{sid}/confirm-step", json={"step_index": 0}).status_code == 409
    for i in range(5, 10):
        s = client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i}).json()
    assert s["state"] == "wiring_complete"

    g = client.post(f"/sessions/{sid}/generate").json()
    code = g["sketch"]["code"]
    assert "const int GATE_PIN = 2;" in code and "const int GATE_ACTIVE_LEVEL = LOW;" in code
    assert "digitalRead(GATE_PIN) != GATE_ACTIVE_LEVEL" in code and "analogRead(MIC_PIN)" in code
    assert g["parameters"]["inputs"] == ["KY-037 microphone sound sensor on A0", "KY-032 infrared obstacle sensor on D2"]
    assert "infrared sensor" in g["display_summary"] and g["flashed"] is False
    # The finished build is untouched; the new sketch is a new build that needs its own approval.
    assert g["build_id"] != first["build_id"]
    assert client.get(f"/builds/{first['build_id']}").json()["status"] == "done"
    assert client.get(f"/builds/{g['build_id']}").json()["status"] == "awaiting_approval"


def test_changing_behaviour_needs_the_ai_and_then_skips_wiring():
    sid, first = finished_level_meter()
    r = extend(sid, "Make the bar blink when it is very loud", expect=422)
    assert "needs the AI service" in r["detail"]
    assert client.get(f"/sessions/{sid}/status").json()["state"] == "code_generated"      # nothing changed

    blinking = first["sketch"]["code"].replace("  matrix.draw(frame);", "  if (shown < COLUMNS || millis() % 200 < 100) matrix.draw(frame);")
    fake_ai = example.use_fake_ai({"kind": "change_behavior", "component_id": "none", "reason": "",
                                   "behavior": "Show the sound level as before, and blink the whole bar when it is full."})
    fake_gen = example.use_fake_codegen({"source_code": blinking, "explanation": "It blinks when full.",
                                         "display_summary": "The bar blinks when it gets very loud.", "assumptions": [],
                                         "expected_serial_events": [{"event": "READY", "when": "start"}]})
    s = extend(sid, "Make the bar blink when it is very loud")
    assert s["state"] == "wiring_complete" and len(s["wiring_steps"]) == 5 and s["pending_confirmations"] == []
    assert "KNOWN PARTS THAT COULD BE ADDED" in fake_ai.requests[0]["messages"][0]["content"][0]["text"]

    g = client.post(f"/sessions/{sid}/generate").json()
    assert g["source"] == "llm" and g["sketch"]["code"] == blinking and g["display_summary"] == "The bar blinks when it gets very loud."
    task = fake_gen.requests[0]["messages"][0]["content"][0]["text"]
    for needed in ("WHAT IT MUST DO NOW", "blink the whole bar when it is full", "Make the bar blink when it is very loud",
                   "THE SKETCH THAT IS RUNNING NOW", "analogRead(A0) only", "LED_BUILTIN", "const int MIC_PIN = A0;"):
        assert needed in task, needed


def test_requests_that_cannot_be_built_change_nothing():
    sid, g = generated_level_meter()
    example.use_fake_ai({"kind": "unsupported", "component_id": "none", "behavior": "", "reason": "A servo is not a known part."},
                        {"kind": "add_part", "component_id": "ky-037", "behavior": "x", "reason": ""})
    assert "servo" in extend(sid, "add a servo", expect=422)["detail"].lower()
    extend(sid, "add another microphone", expect=422)                    # a part that is already in the build
    assert client.get(f"/sessions/{sid}/status").json()["project"]["behavior"] == "level_meter"

    fresh = client.post("/sessions", json={"instruction": "Build a sound level meter"}).json()["session_id"]
    extend(fresh, ADD_IR, expect=409)                                    # nothing built yet
    client.post(f"/builds/{g['build_id']}/approve", json={"sketch_sha256": g["sketch"]["sha256"]})
    extend(sid, ADD_IR, expect=409)                                      # an upload is in progress


def test_gated_template_passes_and_custom_checks_reject_misuse():
    ctx = gated_ctx()
    assert codecheck.check_sketch(GATED, ctx) == []
    for old, new, rule in [
        ("const int GATE_PIN = 2;", "const int GATE_PIN = 3;", "disallowed_pin"),
        ("pinMode(GATE_PIN, INPUT);", "pinMode(GATE_PIN, OUTPUT);", "unsafe_operation"),
        ("pinMode(GATE_PIN, INPUT);", "pinMode(GATE_PIN, INPUT); digitalWrite(GATE_PIN, HIGH);", "unsafe_operation"),
        ("digitalRead(GATE_PIN)", "analogRead(GATE_PIN)", "disallowed_pin"),
        ("digitalRead(GATE_PIN)", "HIGH", "missing_input"),
        ("analogRead(MIC_PIN)", "digitalRead(MIC_PIN)", "disallowed_pin"),
        ("matrix.clear();\n", "matrix.clear();\n  Serial.begin(9600);\n", "disallowed_pin"),
        ('Monitor.println("READY");', "", "missing_event"),
    ]:
        assert old in GATED, old
        issues = codecheck.check_sketch(GATED.replace(old, new, 1), ctx)
        assert rule in {i.rule for i in issues}, (old, [f"{i.rule}: {i.message}" for i in issues])
    # The built-in LED may be used as an output in a developed-further build.
    with_led = GATED.replace("pinMode(GATE_PIN, INPUT);", "pinMode(GATE_PIN, INPUT); pinMode(LED_BUILTIN, OUTPUT); digitalWrite(LED_BUILTIN, HIGH);")
    assert codecheck.check_sketch(with_led, ctx) == []


def test_both_sensors_on_the_3v3_rail_are_safe_and_5v_is_not():
    steps = planner.plan_wiring(planner.level_meter_spec())
    steps += planner.plan_extension(steps, "ky-032")
    assert facts.check_safety(steps) == []
    steps[7].connection.to_pin.pin = "5V"
    assert any("OUT can reach 5.5 V but uno-q D2 tolerates 5.0 V" in p for p in facts.check_safety(steps))


@pytest.mark.skipif(os.getenv("LEARNLENS_REAL_COMPILE") != "1", reason="set LEARNLENS_REAL_COMPILE=1")
def test_gated_level_meter_compiles_with_the_real_uno_q_core(tmp_path, monkeypatch):
    monkeypatch.delenv("LEARNLENS_ARDUINO_CLI")
    monkeypatch.setattr(codegen, "PROJECTS_DIR", tmp_path)
    ctx = gated_ctx()
    report = compiler.ArduinoCliCompiler().compile(codegen.save_project("abc123", 0, codegen.template_sketch(ctx), ctx), ctx.fqbn)
    assert report.status == CompileStatus.ok, report.log[-1500:]
