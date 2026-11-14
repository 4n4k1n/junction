"""Goal first: Pong and Snake with the joystick, switching between them, and a restart by the infrared sensor."""
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import codegen, compiler, facts, games, planner, session_router
from app.build.analyzer import Analyzer, analysis_schema, game_proposal, to_spec
from app.build.models import CompileStatus
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import example_clap_session as example  # noqa: E402

client = TestClient(app)
PARTS = '[{"component_id": "uno-q"}, {"component_id": "ky-023", "confirmations": ["ky023-pin-labels"]}]'
IR = '[{"component_id": "ky-032", "confirmations": ["ky032-pin-labels"]}]'
ADD_RESET = "Add the infrared sensor so the game restarts when I hold my hand over it"


@pytest.fixture(autouse=True)
def games_offered(monkeypatch):
    monkeypatch.setenv("LEARNLENS_OFFERED_PROJECTS", "pong,snake")
    session_router.analyzer = Analyzer(mode="mock")
    session_router.generator = codegen.CodeGenerator(mode="mock")


def post(path, expect=200, **kwargs):
    r = client.post(path, **kwargs)
    assert r.status_code == expect, r.text
    return r.json()


def start(goal="I want to build a Pong game"):
    sid = post("/sessions", json={"instruction": goal})["session_id"]
    return sid, post(f"/sessions/{sid}/analyze")


def wire(sid, s):
    """Confirm every open wiring step and generate."""
    for i in range(s["current_step"], len(s["wiring_steps"])):
        s = post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    assert s["state"] == "wiring_complete"
    return post(f"/sessions/{sid}/generate")


def built(goal="I want to build a Pong game"):
    sid, _ = start(goal)
    return sid, wire(sid, post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": PARTS}))


def extend(sid, text):
    return post(f"/sessions/{sid}/extend", json={"instruction": text})


def spec_for(game, with_reset=False):
    spec = to_spec(game_proposal(game))
    steps = planner.plan_wiring(spec)
    if with_reset:
        steps += planner.plan_extension(steps, "ky-032")
        spec.components.append("ky-032")
        spec.custom_behavior = games.behavior(game, reset=True)
    return spec, steps


def test_a_spoken_goal_gives_a_parts_list_without_any_photo():
    sid, s = start()
    assert s["state"] == "needs_confirmation" and s["analysis_source"] == "rules"
    assert s["project"]["game"] == "pong" and s["project"]["behavior"] == "custom"
    assert s["parts_needed"] == ["Arduino UNO Q", "KY-023 joystick"]
    assert [p["id"] for p in s["pending_confirmations"]] == ["uno-q-present", "ky-023-present", "ky023-pin-labels"]
    assert s["pending_confirmations"][1]["question"] == "For this you need the KY-023 joystick. Do you have one on the desk?"
    assert s["wiring_steps"] == []                                    # nothing is planned before the parts are confirmed

    s = post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": PARTS})
    assert s["state"] == "wiring" and facts.check_safety(planner.plan_wiring(to_spec(game_proposal("pong")))) == []
    assert [(w["connection"]["from_pin"]["pin"], w["connection"]["to_pin"]["pin"]) for w in s["wiring_steps"]] == [
        ("GND", "GND"), ("+5V", "3V3"), ("VRx", "A0"), ("VRy", "A1")]
    assert "runs on 3.3 volts here" in s["wiring_steps"][1]["spoken_text"]


def test_pong_is_generated_from_the_prevalidated_sketch_and_waits_for_approval():
    sid, g = built()
    assert g["source"] == "template" and g["flashed"] is False
    assert "const int PADDLE = 3;" in g["sketch"]["code"] and "RESET_PIN" not in g["sketch"]["code"]
    assert [e["event"] for e in g["expected_serial_events"]] == ["READY", "NEW_GAME", "HIT", "MISS"]
    assert "paddle" in g["display_summary"] and len(g["display_summary"]) <= 180
    assert client.get(f"/builds/{g['build_id']}").json()["status"] == "awaiting_approval"


def test_snake_instead_changes_the_code_and_keeps_the_wiring():
    sid, g = built()
    s = extend(sid, "Snake instead")
    assert s["state"] == "wiring_complete" and s["build_id"] is None
    assert (s["project"]["game"], s["project"]["title"]) == ("snake", "Snake")
    assert len(s["wiring_steps"]) == 4 and s["project"]["components"] == ["uno-q", "ky-023"]
    g2 = post(f"/sessions/{sid}/generate")
    assert "GAME_OVER" in g2["sketch"]["code"] and g2["sketch"]["sha256"] != g["sketch"]["sha256"]
    assert client.get(f"/builds/{g2['build_id']}").json()["status"] == "awaiting_approval"     # a new approval
    assert built("let's play snake")[1]["sketch"]["code"] == g2["sketch"]["code"]              # same as starting with it


def test_the_infrared_sensor_is_added_to_restart_the_game():
    sid, _ = built()
    s = extend(sid, "add the infrared sensor")                         # no purpose given: ask, with the idea that works
    assert s["clarification"] and s["suggestions"] == [games.RESET_IDEA] and s["state"] == "code_generated"
    s = extend(sid, ADD_RESET)
    assert s["state"] == "needs_confirmation" and s["project"]["components"] == ["uno-q", "ky-023", "ky-032"]
    assert s["project"]["game"] == "pong" and "infrared sensor newly detects a hand" in s["project"]["custom_behavior"]
    s = post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": IR})
    assert s["state"] == "wiring" and s["current_step"] == 4           # only the new wires are open
    assert s["wiring_steps"][-1]["display_text"] == "KY-032 OUT to board D2. Plug USB in"
    assert facts.check_safety([planner.WiringStep.model_validate(w) for w in s["wiring_steps"]]) == []
    g = wire(sid, s)
    assert g["source"] == "template" and "const int RESET_PIN = 2;" in g["sketch"]["code"]
    assert "Hold a hand over the infrared sensor to restart" in g["display_summary"]
    s = extend(sid, "I want Snake instead of Pong")                    # the restart stays when the game changes
    assert s["project"]["game"] == "snake" and "newly detects a hand" in s["project"]["custom_behavior"]
    assert "RESET_PIN" in post(f"/sessions/{sid}/generate")["sketch"]["code"]


def test_other_wishes_are_refused_with_what_works():
    _, s = start("build me a weather station")
    assert s["state"] == "unsupported" and s["wiring_steps"] == []
    assert s["project"]["unsupported_reason"] == "Right now I can build Pong or Snake with the joystick. Ask me for one of them."
    sid, _ = built()
    s = extend(sid, "make the ball twice as fast")                     # free-form change: not without the AI service
    assert "needs the AI service" in s["extension_problem"] and s["project"]["game"] == "pong"
    assert s["suggestions"] == ["Play Snake instead", "Add the infrared sensor to restart the game"]


def test_claude_can_name_the_game_but_the_parts_come_from_rules():
    fake = example.use_fake_ai({"components": [], "project": {
        "title": "Bat and ball", "summary": "x", "behavior": "unsupported", "input_component": "none",
        "unsupported_reason": "", "game": "pong"}})
    _, s = start("that old game with a bat and a ball")
    assert len(fake.requests) == 1 and s["project"]["game"] == "pong" and s["analysis_source"] == "llm"
    assert s["project"]["components"] == ["uno-q", "ky-023"] and s["project"]["title"] == "Pong"
    assert analysis_schema()["properties"]["project"]["properties"]["game"]["enum"] == ["pong", "snake", "none"]
    _, s = start("Pong please")                                        # named outright: no model call needed
    assert len(fake.requests) == 1 and s["analysis_source"] == "rules"


def test_with_ai_the_game_is_written_by_claude_and_free_changes_drop_the_fallback():
    sketch = dict(example.CANNED_SKETCH, source_code=codegen.template_sketch(
        codegen.build_context(*spec_for("pong"))).source_code)
    fake = example.use_fake_codegen(sketch, sketch)
    sid, g = built()
    task = fake.requests[0]["messages"][0]["content"]
    task = task if isinstance(task, str) else task[0]["text"]
    assert g["source"] == "llm" and "A one-player Pong game" in task
    assert "analogRead(A0) only" in task and "analogRead(A1) only" in task and "digitalWrite on anything but" in task
    example.use_fake_ai({"kind": "change_behavior", "component_id": "none", "reason": "", "needs_purpose": False,
                         "behavior": "Pong as before, but the ball moves twice as fast.", "ideas": []})
    s = extend(sid, "make the ball twice as fast")
    assert s["state"] == "wiring_complete" and s["project"]["game"] == ""          # no prevalidated sketch for this
    assert s["project"]["custom_behavior"] == "Pong as before, but the ball moves twice as fast."


def test_the_user_is_told_once_that_the_code_is_written_while_they_wire(monkeypatch):
    monkeypatch.setattr(session_router, "PREGENERATE", True)
    sid, _ = start()
    s = post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": PARTS})
    assert s["background_notice"] == "I am writing the code in the background while you wire."
    session_router.pregenerated[sid][2].result(timeout=10)
    assert "Loading the prevalidated code" in client.get(f"/sessions/{sid}/status").json()["generation_progress"]
    for i in range(4):
        s = post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    assert s["state"] == "wiring_complete" and s["background_notice"] == ""
    assert post(f"/sessions/{sid}/generate")["source"] == "template"   # ready: straight to the approval


def test_demo_mode_builds_the_games_and_says_so(monkeypatch):
    monkeypatch.setenv("LEARNLENS_DEMO_MODE", "1")
    session_router.analyzer, session_router.generator = Analyzer(), codegen.CodeGenerator()
    sid, g = built("Snake")
    assert g["source"] == "demo" and "GAME_OVER" in g["sketch"]["code"]
    assert client.get(f"/sessions/{sid}/status").json()["analysis_source"] == "demo"
    _, s = start("a clap light")
    assert s["state"] == "unsupported" and "Pong or Snake" in s["project"]["unsupported_reason"]


@pytest.mark.parametrize("game,with_reset", [("pong", False), ("pong", True), ("snake", False), ("snake", True)])
def test_every_prevalidated_game_sketch_passes_the_static_checks(game, with_reset, tmp_path, monkeypatch):
    spec, steps = spec_for(game, with_reset)
    assert facts.check_safety(steps) == []
    ctx = codegen.build_context(spec, steps)
    sketch = codegen.template_sketch(ctx)
    assert codegen.check_response(sketch, ctx) == []
    assert ("RESET_PIN" in sketch.source_code) == with_reset and "digitalWrite" not in sketch.source_code
    if os.getenv("LEARNLENS_REAL_COMPILE") == "1":
        monkeypatch.delenv("LEARNLENS_ARDUINO_CLI")
        monkeypatch.setattr(codegen, "PROJECTS_DIR", tmp_path)
        report = compiler.ArduinoCliCompiler().compile(codegen.save_project("abc123", 0, sketch, ctx), ctx.fqbn)
        assert report.status == CompileStatus.ok, report.log[-1500:]


JPEG = {"image": ("part.jpg", b"\xff\xd8\xff\xe0 not a real photo", "image/jpeg")}
SEEN_IR = {"components": [{"component_id": "ky-032", "label": "board with two LEDs at the front", "confidence": 0.9,
                           "evidence": "two LEDs, two trim pots"}],
           "project": {"title": "x", "summary": "x", "behavior": "unsupported", "input_component": "none",
                       "unsupported_reason": "", "game": "none"}}


def test_a_photo_of_the_new_part_says_which_part_this_sensor_is():
    sid, _ = built()
    fake = example.use_fake_ai(SEEN_IR, {"kind": "add_part", "component_id": "ky-032", "reason": "", "ideas": [],
                                         "needs_purpose": False, "behavior": "Pong; a hand restarts the game."})
    s = post(f"/sessions/{sid}/analyze", data={"purpose": "part"}, files=JPEG)
    assert s["state"] == "code_generated" and [d["component_id"] for d in s["detected_components"]] == ["ky-032"]
    assert s["project"]["components"] == ["uno-q", "ky-023"]                      # a photo adds nothing by itself
    s = extend(sid, "I want to add that sensor so the game restarts when my hand is over it")
    asked = fake.requests[1]["messages"][0]["content"][0]["text"]
    assert "A PHOTO OF THE PART THEY WANT TO ADD WAS TAKEN" in asked and "ky-032 (KY-032 infrared obstacle sensor)" in asked
    assert s["state"] == "needs_confirmation" and s["project"]["game"] == "pong"
    assert [p["id"] for p in s["pending_confirmations"]] == ["ky032-pin-labels"]   # seen in the photo; labels still asked
    assert sid not in session_router.part_seen                                    # used once


def test_without_a_named_or_photographed_part_the_user_is_asked_to_name_it():
    sid, _ = built()
    post(f"/sessions/{sid}/analyze", data={"purpose": "part"}, files=JPEG)        # no AI: the photo shows nothing
    s = extend(sid, "I want to add that sensor")
    assert "could not tell which part" in s["extension_problem"] and s["state"] == "code_generated"
    post(f"/sessions/{sid}/analyze", expect=422, data={"purpose": "part"})        # a part photo needs a photo


def test_a_pause_request_is_not_turned_into_a_restart():
    sid, _ = built()
    example.use_fake_codegen()
    example.use_fake_ai({"kind": "add_part", "component_id": "ky-032", "reason": "", "needs_purpose": False, "ideas": [],
                         "behavior": "Pong as before; a miss starts a new game. The game pauses while a hand is near."})
    s = extend(sid, "add the infrared sensor. Pause the game while a hand is in front of the sensor")
    assert s["state"] == "needs_confirmation" and s["project"]["game"] == ""       # free-form: written by Claude
    assert s["project"]["custom_behavior"].endswith("The game pauses while a hand is near.")


def test_the_sound_level_meter_is_offered_next_to_the_games(monkeypatch):
    monkeypatch.delenv("LEARNLENS_OFFERED_PROJECTS")
    _, s = start("I want to build a sound level meter")
    assert s["state"] == "needs_confirmation" and s["project"]["behavior"] == "level_meter"
    assert s["parts_needed"] == ["Arduino UNO Q", "KY-037 microphone sound sensor"]
    assert start()[1]["project"]["game"] == "pong"
    _, s = start("a weather station")
    assert "Pong or Snake with the joystick, or a sound level meter" in s["project"]["unsupported_reason"]


def proposal(parts, what="Show the brightness as a bar on the LED matrix. A button press clears it.", **more):
    return {"components": [], "project": dict({
        "title": "Light meter", "summary": "Shows how bright it is.", "behavior": "unsupported",
        "input_component": "none", "unsupported_reason": "", "game": "none", "parts": parts, "what": what}, **more)}


def test_any_wish_becomes_a_project_from_known_parts_planned_by_rules():
    fake = example.use_fake_ai(proposal(["ky-018", "ky-004"]))
    sid, s = start("a light meter I can clear with a button")
    asked = fake.requests[0]["messages"][0]["content"][0]["text"]
    assert "BUILDABLE PARTS:" in asked and "ky-018 (KY-018 photoresistor): senses brightness" in asked
    assert "KY-019 relay: It needs 5 volts" in asked                             # so it can say why something is impossible
    enum = fake.requests[0]["output_config"]["format"]["schema"]["properties"]["project"]["properties"]["parts"]["items"]["enum"]
    assert "ky-018" in enum and "ky-019" not in enum and "uno-q" not in enum
    assert s["state"] == "needs_confirmation" and s["project"]["behavior"] == "custom" and s["project"]["game"] == ""
    assert s["parts_needed"] == ["Arduino UNO Q", "KY-018 photoresistor", "KY-004 button"]
    assert s["project"]["custom_behavior"].startswith("Show the brightness as a bar")

    inventory = ('[{"component_id": "uno-q"}, {"component_id": "ky-018", "confirmations": ["ky018-pin-labels"]},'
                 ' {"component_id": "ky-004", "confirmations": ["ky004-pin-labels"]}]')
    s = post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": inventory})
    assert s["state"] == "wiring" and s["safety_problems"] == []
    assert [w["connection"]["to_pin"]["pin"] for w in s["wiring_steps"]] == ["GND", "3V3", "A0", "GND", "3V3", "D2"]
    assert "(shared)" in s["wiring_steps"][4]["display_text"]

    written = dict(example.CANNED_SKETCH, source_code=codegen.template_sketch(
        codegen.build_context(*spec_for("pong"))).source_code.replace("A1", "A0").replace(
        "void loop() {", "const int BUTTON = 2;\nvoid loop() {\n  pinMode(BUTTON, INPUT);\n  digitalRead(BUTTON);"))
    code = example.use_fake_codegen(written, written, written)
    for i in range(6):
        post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    r = client.post(f"/sessions/{sid}/generate")
    task = code.requests[0]["messages"][0]["content"]
    task = task if isinstance(task, str) else task[0]["text"]
    assert "WHAT THE BUILD MUST DO:\nShow the brightness as a bar" in task and "A new build" in task
    assert "KY-018 photoresistor, analog output on header A0" in task and "KY-004 button, digital output on header D2" in task
    assert r.status_code == 200 and r.json()["source"] == "llm", r.text


def test_a_free_project_cannot_bring_in_parts_or_pins_the_database_does_not_allow():
    example.use_fake_ai(proposal(["ky-019"]), proposal(["ky-004", "ky-017", "ky-020", "ky-021"]),
                        proposal(["ky-040", "ky-004", "ky-017"]), proposal(["esp32"]), proposal([], what=""))
    _, s = start("switch a lamp")                                     # a part that is known but not buildable
    assert s["state"] == "unsupported" and s["project"]["unsupported_reason"] == facts.catalog()["ky-019"]["not_buildable"]
    _, s = start("four switches")
    assert s["state"] == "unsupported" and "more than 3 parts" in s["project"]["unsupported_reason"]
    sid, s = start("knob, button and tilt")                           # 3 + 1 + 1 digital signals, 4 free inputs
    assert s["state"] == "needs_confirmation"
    everything = [{"component_id": "uno-q"}] + [{"component_id": c, "confirmations": [c.replace("-", "") + "-pin-labels"]}
                                                 for c in ("ky-040", "ky-004", "ky-017")]
    import json
    s = post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": json.dumps(everything)})
    assert s["state"] == "unsupported" and "more input pins" in s["spoken_text"] and s["wiring_steps"] == []
    _, s = start("something with an esp32")
    assert s["state"] == "unsupported" and s["project"]["components"] == ["uno-q"]
    _, s = start("a robot arm")
    assert s["state"] == "unsupported"


def test_without_ai_a_free_wish_is_refused_with_what_works(monkeypatch):
    _, s = start("a light meter")                                     # mock analyzer: no model to propose parts
    assert s["state"] == "unsupported" and "Pong or Snake" in s["project"]["unsupported_reason"]
    monkeypatch.setenv("LEARNLENS_FREE_PROJECTS", "0")                 # switched off: even a proposal is not used
    example.use_fake_ai(proposal(["ky-018"]))
    assert start("a light meter")[1]["state"] == "unsupported"
