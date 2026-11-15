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
    assert len(s["wiring_steps"]) == 6 and s["confirmed_steps"] == [0, 1, 2]

    confirmed = '[{"component_id": "ky-032", "confirmations": ["ky032-pin-labels"]}]'
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": confirmed}).json()
    assert s["state"] == "wiring" and s["current_step"] == 3                     # the first new step
    new = [(c["from_pin"]["pin"], c["to_pin"]["pin"]) for c in (st["connection"] for st in s["wiring_steps"][3:]) if c]
    assert new == [("GND", "GND"), ("+", "3V3"), ("OUT", "D2")]                  # 3.3 V again, a free digital pin
    assert "only one 3.3 volt pin" in s["wiring_steps"][4]["spoken_text"]
    assert s["wiring_steps"][3]["display_text"].startswith("Unplug USB.") and s["wiring_steps"][5]["display_text"].endswith("Plug USB in")
    assert client.post(f"/sessions/{sid}/confirm-step", json={"step_index": 0}).status_code == 409
    for i in range(3, 6):
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
    r = extend(sid, "Make the bar blink when it is very loud")
    assert "needs the AI service" in r["extension_problem"]                               # said why ...
    assert r["suggestions"][0] == "Show the sound level only while a hand is near"        # ... and what would work
    assert r["state"] == "code_generated" and r["project"]["behavior"] == "level_meter"   # nothing changed
    assert extend(sid, r["suggestions"][0])["state"] == "needs_confirmation"              # a suggestion is a full request
    sid, first = finished_level_meter()

    blinking = first["sketch"]["code"].replace("  matrix.draw(frame);", "  if (shown < COLUMNS || millis() % 200 < 100) matrix.draw(frame);")
    fake_ai = example.use_fake_ai({"kind": "change_behavior", "component_id": "none", "reason": "",
                                   "behavior": "Show the sound level as before, and blink the whole bar when it is full."})
    fake_gen = example.use_fake_codegen({"source_code": blinking, "explanation": "It blinks when full.",
                                         "display_summary": "The bar blinks when it gets very loud.", "assumptions": [],
                                         "expected_serial_events": [{"event": "READY", "when": "start"}]})
    s = extend(sid, "Make the bar blink when it is very loud")
    assert s["state"] == "wiring_complete" and len(s["wiring_steps"]) == 3 and s["pending_confirmations"] == []
    assert "KNOWN PARTS THAT COULD BE ADDED" in fake_ai.requests[0]["messages"][0]["content"][0]["text"]

    g = client.post(f"/sessions/{sid}/generate").json()
    assert g["source"] == "llm" and g["sketch"]["code"] == blinking and g["display_summary"] == "The bar blinks when it gets very loud."
    task = fake_gen.requests[0]["messages"][0]["content"][0]["text"]
    for needed in ("WHAT IT MUST DO NOW", "blink the whole bar when it is full", "Make the bar blink when it is very loud",
                   "THE SKETCH THAT IS RUNNING NOW", "analogRead(A0) only", "LED_BUILTIN", "const int MIC_PIN = A0;"):
        assert needed in task, needed


def test_requests_that_cannot_be_built_change_nothing():
    sid, g = generated_level_meter()
    fake = example.use_fake_ai(
        {"kind": "unsupported", "component_id": "none", "behavior": "", "reason": "A servo is not a known part.",
         "needs_purpose": False, "ideas": ["Add the IR sensor: wave to pause the meter", "Blink the bar when it is loud"]},
        {"kind": "add_part", "component_id": "ky-037", "behavior": "x", "reason": ""})
    r = extend(sid, "add a servo")
    assert r["extension_problem"] == "A servo is not a known part."      # the reason, in the model's words
    assert r["suggestions"] == ["Add the IR sensor: wave to pause the meter", "Blink the bar when it is loud"]
    assert r["clarification"] == "" and r["state"] == "code_generated"
    assert "three requests that WOULD work instead" in fake.requests[0]["system"][0]["text"]
    r = extend(sid, "add another microphone")                            # a part that is already in the build
    assert "already part of this build" in r["extension_problem"] and len(r["suggestions"]) == 3   # fallback ideas
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
    steps[4].connection.to_pin.pin = "5V"
    assert any("OUT can reach 5.5 V but uno-q D2 tolerates 5.0 V" in p for p in facts.check_safety(steps))


@pytest.mark.skipif(os.getenv("LEARNLENS_REAL_COMPILE") != "1", reason="set LEARNLENS_REAL_COMPILE=1")
def test_gated_level_meter_compiles_with_the_real_uno_q_core(tmp_path, monkeypatch):
    monkeypatch.delenv("LEARNLENS_ARDUINO_CLI")
    monkeypatch.setattr(codegen, "PROJECTS_DIR", tmp_path)
    ctx = gated_ctx()
    report = compiler.ArduinoCliCompiler().compile(codegen.save_project("abc123", 0, codegen.template_sketch(ctx), ctx), ctx.fqbn)
    assert report.status == CompileStatus.ok, report.log[-1500:]


def test_a_new_photo_of_the_added_part_updates_detection_but_not_the_project():
    sid, _ = finished_level_meter()
    extend(sid, ADD_IR)
    seen = {"components": [{"component_id": "ky-032", "label": "board with two LEDs at the front", "confidence": 0.93,
                            "evidence": "Two LEDs side by side, two blue trim pots, a jumper."}],
            "project": {"title": "Something else", "summary": "x", "behavior": "unsupported",
                        "input_component": "none", "unsupported_reason": "not relevant"}}
    example.use_fake_ai(seen)
    photo = {"image": ("part.jpg", __import__("io").BytesIO(example.FAKE_JPEG), "image/jpeg")}
    s = client.post(f"/sessions/{sid}/analyze", files=photo).json()
    assert s["project"]["behavior"] == "custom" and s["project"]["components"][-1] == "ky-032"   # project untouched
    assert [d["component_id"] for d in s["detected_components"]] == ["ky-032"]
    # Seen with high confidence: no presence question, but the pin labels are still asked.
    assert s["state"] == "needs_confirmation" and [p["id"] for p in s["pending_confirmations"]] == ["ky032-pin-labels"]


def test_a_part_without_a_purpose_gets_a_question_back_and_changes_nothing():
    sid, first = finished_level_meter()
    s = extend(sid, "I want to add the infrared sensor")
    assert s["clarification"] == "What should the KY-032 infrared obstacle sensor do in your build?"
    assert s["state"] == "code_generated" and s["build_id"] == first["build_id"]
    assert s["project"]["behavior"] == "level_meter" and len(s["wiring_steps"]) == 3
    assert s["suggestions"] == ["Show the sound level only while a hand is near", "Hold a hand near to freeze the level",
                                "Wave a hand to switch the meter on and off"]     # offline: only what is prevalidated

    s = extend(sid, "I want to add the infrared sensor. " + s["suggestions"][0])     # picking a suggestion is an answer
    assert s["clarification"] == "" and s["suggestions"] == [] and s["state"] == "needs_confirmation"
    assert len(s["wiring_steps"]) == 6

    # With AI the model decides, and is told not to invent a purpose.
    sid, _ = finished_level_meter()
    fake = example.use_fake_ai({"kind": "add_part", "component_id": "ky-032", "behavior": "", "reason": "",
                                "needs_purpose": True,
                                "ideas": ["Show the meter only when a hand is near", "  Wave to freeze the bar ",
                                          "Blink when something blocks the sensor", "a fourth idea", ""]})
    s = extend(sid, "add the IR sensor")
    assert s["clarification"].startswith("What should the KY-032")
    assert s["suggestions"] == ["Show the meter only when a hand is near", "Wave to freeze the bar",
                                "Blink when something blocks the sensor"]                  # at most three, tidied
    assert "Do not invent a\npurpose" in fake.requests[0]["system"][0]["text"]


def variant_ctx(idea):
    spec = planner.level_meter_spec()
    steps = planner.plan_wiring(spec)
    steps += planner.plan_extension(steps, "ky-032")
    spec.behavior, spec.custom_behavior, spec.requests = Behavior.custom, idea, [idea]
    spec.components.append("ky-032")
    return codegen.build_context(spec, steps)


@pytest.mark.parametrize("variant,marker", [
    ("gate", "if (digitalRead(GATE_PIN) != GATE_ACTIVE_LEVEL) shown = 0;"),
    ("freeze", "the bar is frozen where it is"),
    ("toggle", "if (gateActive && !gateWasActive) meterOn = !meterOn;"),
])
def test_every_offered_idea_has_its_own_prevalidated_sketch(variant, marker, tmp_path, monkeypatch):
    from app.build import variants as demo

    idea = demo.EXTENSION_VARIANTS[variant]["idea"]
    assert len(idea) <= 60 and demo.variant_for("I want to add the infrared sensor. " + idea) == variant
    ctx = variant_ctx(demo.EXTENSION_VARIANTS[variant]["behavior"])
    sketch = codegen.template_sketch(ctx)
    assert marker in sketch.source_code and codecheck.check_sketch(sketch.source_code, ctx) == []
    assert sketch.display_summary == demo.EXTENSION_VARIANTS[variant]["summary"]
    if os.getenv("LEARNLENS_REAL_COMPILE") == "1":
        monkeypatch.delenv("LEARNLENS_ARDUINO_CLI")
        monkeypatch.setattr(codegen, "PROJECTS_DIR", tmp_path)
        report = compiler.ArduinoCliCompiler().compile(codegen.save_project("abc123", 0, sketch, ctx), ctx.fqbn)
        assert report.status == CompileStatus.ok, report.log[-1500:]


def test_the_second_sensor_flow_runs_without_ai():
    sid, _ = finished_level_meter()                                      # mock analyzer and generator: no model at all

    s = extend(sid, "I would like to add the infrared sensor")           # named, but no purpose: ask, with ideas
    assert s["clarification"].startswith("What should the KY-032") and len(s["suggestions"]) == 3
    s = extend(sid, "I would like to add the infrared sensor. " + s["suggestions"][2])
    assert s["state"] == "needs_confirmation" and s["project"]["components"][-1] == "ky-032"
    assert [p["id"] for p in s["pending_confirmations"]] == ["ky-032-present", "ky032-pin-labels"]

    confirmed = '[{"component_id": "ky-032", "confirmations": ["ky032-pin-labels"]}]'
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": confirmed}).json()
    for i in range(s["current_step"], len(s["wiring_steps"])):
        client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    g = client.post(f"/sessions/{sid}/generate").json()
    assert g["source"] == "template" and "meterOn = !meterOn" in g["sketch"]["code"]
    assert g["display_summary"].startswith("Wave a hand past the infrared sensor")
