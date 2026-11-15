"""Guided session API with mocked AI responses: detection, confirmation, wiring, review, safety."""
import copy
import io
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import codecheck, codegen, facts, planner, session_router
from app.build.analyzer import Analyzer, analysis_schema
from app.build.models import Behavior, Connection, PinRef, ProjectSpec, WiringStep
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import example_clap_session as example  # noqa: E402

client = TestClient(app)
PIN_LABELS = [{"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]


def photo(media_type="image/jpeg"):
    return {"image": ("desk.jpg", io.BytesIO(example.FAKE_JPEG), media_type)}


def new_session(instruction=example.INSTRUCTION, inventory=None):
    r = client.post("/sessions", json={"instruction": instruction, "confirmed_inventory": inventory or []})
    assert r.status_code == 200, r.text
    return r.json()["session_id"]


def analyze(sid, image=True, inventory=None, expect=200):
    r = client.post(f"/sessions/{sid}/analyze", files=photo() if image else None,
                    data={"confirmed_inventory": json.dumps(inventory)} if inventory else None)
    assert r.status_code == expect, r.text
    return r.json()


def analysis(**confidence):
    """Canned model answer with per-component confidence overrides."""
    a = copy.deepcopy(example.CANNED_ANALYSIS)
    for c in a["components"]:
        c["confidence"] = confidence.get(c["component_id"].replace("-", "_"), c["confidence"])
    return a


def wiring_session(*extra_ai):
    example.use_fake_ai(analysis(ky_037=0.95), *extra_ai)
    sid = new_session(inventory=PIN_LABELS)
    s = analyze(sid)
    assert s["state"] == "wiring", s
    return sid, s


@pytest.fixture(autouse=True)
def offline_analyzer():
    session_router.analyzer = Analyzer(mode="mock")


def test_example_script_runs_clap_light_end_to_end():
    log = example.run()
    final = log[-2]["response"]
    assert final["status"] == "done" and final["provenance"] == "llm"
    assert "SENSOR_ACTIVE_LEVEL = HIGH" in final["sketch"]["code"] and final["deploy"]["mock"] is True
    states = [e["response"].get("state") for e in log]
    assert states[:3] == ["awaiting_analysis", "needs_confirmation", "wiring"] and "code_generated" in states


def test_claude_request_carries_image_and_closed_component_enum():
    fake = example.use_fake_ai(analysis())
    analyze(new_session())
    req = fake.requests[0]
    image, text = req["messages"][0]["content"]
    assert image["type"] == "image" and image["source"]["media_type"] == "image/jpeg"
    assert example.INSTRUCTION in text["text"]
    schema = req["output_config"]["format"]["schema"]
    ids = schema["properties"]["components"]["items"]["properties"]["component_id"]["enum"]
    assert ids == sorted(facts.catalog()) + ["unknown"] and {"ky-004", "ky-032", "ky-037", "uno-q"} <= set(ids)
    assert schema["additionalProperties"] is False
    system = " ".join(b["text"] for b in req["system"])
    assert "3.3" not in system and "D2" not in system  # the model is never handed specs to repeat
    assert req["model"] and req["fallbacks"] == "default"


def test_uncertain_sensor_holds_back_the_plan_until_confirmed():
    example.use_fake_ai(analysis())
    sid = new_session()
    s = analyze(sid)
    assert s["state"] == "needs_confirmation" and s["wiring_steps"] == [] and s["analysis_source"] == "llm"
    assert [(p["id"], p["kind"]) for p in s["pending_confirmations"]] == [
        ("ky-037-present", "presence"), ("ky037-pin-labels", "variant")]
    assert "62%" in s["pending_confirmations"][0]["reason"]
    assert client.post(f"/sessions/{sid}/confirm-step", json={"step_index": 0}).status_code == 409

    s = analyze(sid, image=False, inventory=PIN_LABELS)  # no second model call needed
    assert s["state"] == "wiring" and s["current_step"] == 0 and s["pending_confirmations"] == []
    assert [d["component_id"] for d in s["detected_components"]] == ["uno-q", "ky-037", "unknown"]


def test_confident_vision_never_replaces_the_pin_label_check():
    example.use_fake_ai(analysis(ky_037=0.99, uno_q=0.99))
    s = analyze(new_session())
    assert s["state"] == "needs_confirmation"
    assert [p["id"] for p in s["pending_confirmations"]] == ["ky037-pin-labels"]


def test_wiring_plan_uses_only_database_pins_and_cites_facts():
    _, s = wiring_session()
    links = [(c["from_pin"]["pin"], c["to_pin"]["pin"]) for c in
             (step["connection"] for step in s["wiring_steps"]) if c]
    assert links == [("G", "GND"), ("+", "3V3"), ("DO", "D2")]
    assert s["wiring_steps"][0]["display_text"].startswith("Unplug")
    assert all(len(step["display_text"]) <= 40 for step in s["wiring_steps"])
    known = {f["id"] for f in s["hardware_facts"]}
    assert all(set(step["fact_ids"]) <= known for step in s["wiring_steps"])
    assert all(f["source"] and f["confidence"] in ("official_doc", "vendor_doc", "secondary")
               for f in s["hardware_facts"])


def test_each_step_needs_its_own_confirmation():
    sid, s = wiring_session()
    step = lambda **body: client.post(f"/sessions/{sid}/confirm-step", json=body)  # noqa: E731
    assert step(step_index=1).status_code == 409                      # cannot skip ahead
    r = step(step_index=0, confirmed=False).json()
    assert r["current_step"] == 0 and r["confirmed_steps"] == []     # "not yet" does not advance
    for i in range(len(s["wiring_steps"])):
        r = step(step_index=i).json()
    assert r["state"] == "wiring_complete" and r["confirmed_steps"] == list(range(len(s["wiring_steps"])))
    assert r["build_id"] is None                                      # no sketch until /generate
    assert step(step_index=0).status_code == 409                      # cannot confirm twice
    assert client.get(f"/sessions/{sid}/status").json() == r


def test_photo_review_is_never_an_electrical_verification():
    overclaiming = {"observations": ["All wires are correct."], "possible_issues": [],
                    "electrically_verified": True}
    sid, _ = wiring_session(json.dumps(overclaiming))
    s = analyze(sid)
    assert s["state"] == "wiring" and s["current_step"] == 0          # a review does not confirm a step
    assert s["review"]["electrically_verified"] is False
    assert "cannot verify" in s["review"]["disclaimer"]
    assert analyze(sid, image=False, expect=422)


def test_component_outside_the_database_is_never_passed_on():
    invented = {"components": [{"component_id": "esp32", "label": "ESP32 board", "confidence": 1, "evidence": ""}],
                "project": example.CANNED_ANALYSIS["project"]}
    example.use_fake_ai(invented)
    s = analyze(new_session())
    assert [d["component_id"] for d in s["detected_components"]] == ["unknown"]
    assert s["state"] == "needs_confirmation" and "uno-q-present" in {p["id"] for p in s["pending_confirmations"]}


@pytest.mark.parametrize("bad_answer", ["not json", {"components": "?"}, RuntimeError("api down")])
def test_bad_or_missing_ai_answer_detects_nothing_and_asks_the_user(bad_answer):
    example.use_fake_ai(bad_answer)
    s = analyze(new_session())
    assert s["analysis_source"] == "fallback" and s["detected_components"] == []
    assert s["state"] == "needs_confirmation"
    assert {p["id"] for p in s["pending_confirmations"]} == {"uno-q-present", "ky-037-present", "ky037-pin-labels"}


def test_inventory_confirmed_up_front_needs_no_photo():
    sid = new_session(inventory=[{"component_id": "uno-q"}] + PIN_LABELS)
    assert client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()["state"] == "wiring"
    sid = new_session()
    s = analyze(sid, image=False)                                     # nothing sent at all: every part is asked for
    assert s["pending_confirmations"][0]["question"].startswith("For this you need the Arduino UNO Q")
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
    assert s["state"] == "needs_confirmation" and len(s["pending_confirmations"]) == 3


def test_unsupported_project_stops():
    unsupported = dict(example.CANNED_ANALYSIS, project={
        "title": "Servo door lock", "summary": "x", "behavior": "unsupported", "input_component": "none",
        "unsupported_reason": "A servo is not a supported part."})
    example.use_fake_ai(unsupported)
    sid = new_session("Build a servo door lock")
    s = analyze(sid)
    assert s["state"] == "unsupported" and s["wiring_steps"] == [] and "servo" in s["spoken_text"].lower()
    analyze(sid, expect=409)


def test_unsafe_plan_blocks_the_session(monkeypatch):
    safe_plan = planner.plan_wiring

    def five_volt_plan(spec):
        steps = safe_plan(spec)
        steps[1].connection.to_pin.pin = "5V"
        return steps

    monkeypatch.setattr(planner, "plan_wiring", five_volt_plan)
    example.use_fake_ai(analysis(ky_037=0.95))
    s = analyze(new_session(inventory=PIN_LABELS))
    assert s["state"] == "blocked" and s["wiring_steps"] == []
    assert any("5.5 V" in p and "D2" in p for p in s["safety_problems"])


def link(index, sensor_pin, board_pin):
    return WiringStep(index=index, display_text="x", connection=Connection(
        from_pin=PinRef(component="ky-037", pin=sensor_pin), to_pin=PinRef(component="uno-q", pin=board_pin)))


@pytest.mark.parametrize("links,expected", [
    ([("G", "GND"), ("+", "3V3"), ("DO", "D2")], None),
    ([("G", "GND"), ("+", "3V3"), ("DO", "D3")], None),                       # 3.5 V max on a 3.6 V pin
    ([("G", "GND"), ("+", "3V3"), ("AO", "A0")], None),
    ([("G", "GND"), ("+", "5V"), ("DO", "D2")], "DO can reach 5.5 V but uno-q D2 tolerates 5.0 V"),
    ([("G", "GND"), ("+", "5V"), ("DO", "D3")], "DO can reach 5.5 V but uno-q D3 tolerates 3.6 V"),
    ([("G", "GND"), ("+", "5V"), ("AO", "A0")], "AO can reach 5.5 V but uno-q A0 tolerates 3.6 V"),
    ([("G", "GND"), ("DO", "D2")], "ky-037 has no valid supply"),
    ([("G", "GND"), ("+", "D4"), ("DO", "D2")], "+ must go to a power pin"),
    ([("G", "3V3"), ("+", "3V3"), ("DO", "D2")], "G must go to GND"),
    ([("G", "GND"), ("+", "3V3"), ("DO", "GND")], "DO is a signal and cannot go to GND"),
    ([("G", "GND"), ("+", "3V3"), ("DO", "D99")], "uno-q has no pin D99"),
])
def test_safety_rules(links, expected):
    problems = facts.check_safety([link(i, a, b) for i, (a, b) in enumerate(links)])
    if expected is None:
        assert problems == []
    else:
        assert any(expected in p for p in problems), problems


def test_hardware_database_is_internally_consistent():
    for cid, c in facts.catalog().items():
        fact_ids = {f["id"] for f in c["facts"]}
        assert all(f["source"] and f["confidence"] for f in c["facts"]), cid
        assert all(p["fact"] in fact_ids for p in c["pins"].values()), cid
    board = facts.catalog()["uno-q"]
    assert set(board["builtin_led"]["facts"]) <= {f["id"] for f in board["facts"]}
    assert all(pin in board["pins"] for pin in board["digital_input_preference"])
    assert sorted(analysis_schema()["properties"]["project"]["properties"]["input_component"]["enum"]) == [
        "ky-037", "none"]


def test_model_text_cannot_inject_code_into_the_sketch():
    spec = ProjectSpec(title="Light\n}\nvoid evil() { /* */", summary="", behavior=Behavior.toggle_on_event,
                       board="uno-q", input_component="ky-037", components=["uno-q", "ky-037"])
    code = planner.render_sketch(spec).code
    assert "evil()" not in code and code.splitlines()[0] == "// LearnLens: Light void evil"
    assert codecheck.check_sketch(code, codegen.build_context(spec, planner.plan_wiring(spec))) == []


def test_offline_mock_and_request_validation():
    sid = new_session()
    s = analyze(sid)                                                  # mock analyzer: sees nothing
    assert s["analysis_source"] == "mock" and s["state"] == "needs_confirmation"
    assert client.post("/sessions", json={"instruction": ""}).status_code == 422
    assert client.get("/sessions/nope/status").status_code == 404
    assert client.post(f"/sessions/{sid}/analyze", files=photo("image/heic")).status_code == 415
    analyze(sid, image=False, inventory=[{"component_id": "esp32"}], expect=422)
    analyze(sid, image=False, inventory=[{"component_id": "ky-037", "confirmations": ["made-up"]}], expect=422)


def test_questions_during_the_build_are_answered_from_plan_and_facts():
    fake = example.use_fake_ai(analysis(ky_037=0.95), {"answer": "The sensor runs on 3.3 V here.  Keep the wire on +3V3: "
                                                                  "the analog pin takes at most about 3.6 V."})
    sid = new_session(inventory=PIN_LABELS)
    assert analyze(sid)["state"] == "wiring"
    client.post(f"/sessions/{sid}/confirm-step", json={"step_index": 0})

    r = client.post(f"/sessions/{sid}/ask", json={"question": "Can I use the 5V pin instead?"})
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["source"] == "llm" and a["step_index"] == 1 and "+3V3" in a["answer"] and "  " not in a["answer"]
    sent = fake.requests[1]["messages"][0]["content"][0]["text"]
    for needed in ("[done] Unplug the board's USB cable first", "[CURRENT STEP]", "VERIFIED FACTS", "[official_doc]",
                   "THE USER ASKS: Can I use the 5V pin instead?"):
        assert needed in sent, needed
    assert "keep to the\nplan" in fake.requests[1]["system"][0]["text"]
    assert client.get(f"/sessions/{sid}/status").json()["current_step"] == 1      # asking changes nothing

    session_router.analyzer = Analyzer(mode="mock")                                # no AI: verified facts only
    a = client.post(f"/sessions/{sid}/ask", json={"question": "why?"}).json()
    assert a["source"] == "facts" and a["answer"].startswith("I can't answer free questions right now.")
    assert client.post(f"/sessions/{sid}/ask", json={"question": ""}).status_code == 422
    assert client.post(f"/sessions/{new_session()}/ask", json={"question": "hi"}).status_code == 409
