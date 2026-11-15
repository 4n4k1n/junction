"""The whole KY sensor kit is in the hardware database: which parts can be added, and why the others cannot."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import codegen, facts, planner, session_router
from app.build.analyzer import Analyzer, addable_sensors, keyword_extension, not_buildable
from app.build.models import Behavior
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import example_clap_session as example  # noqa: E402

client = TestClient(app)
INVENTORY = [{"component_id": "uno-q"}, {"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]
KIT = [cid for cid in facts.catalog() if cid.startswith("ky-")]
ADDABLE = [cid for cid in KIT if facts.catalog()[cid].get("add_on") and cid != "ky-037"]


@pytest.fixture(autouse=True)
def offline():
    session_router.analyzer = Analyzer(mode="mock")
    session_router.generator = codegen.CodeGenerator(mode="mock")


def level_meter_plus(cid):
    spec = planner.level_meter_spec()
    steps = planner.plan_wiring(spec)
    new = planner.plan_extension(steps, cid)
    spec.behavior, spec.custom_behavior = Behavior.custom, "x"
    spec.components.append(cid)
    return spec, steps, new


def generated_level_meter():
    sid = client.post("/sessions", json={"instruction": "Build a sound level meter",
                                         "confirmed_inventory": INVENTORY}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
    for i in range(len(s["wiring_steps"])):
        client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    client.post(f"/sessions/{sid}/generate")
    return sid


def test_every_kit_part_is_known_and_either_addable_or_explains_why_not():
    cat = facts.catalog()
    assert len(KIT) == 41 and len(ADDABLE) == 22
    for cid in KIT:
        c = cat[cid]
        assert c["identification"] and c["sources"]["vendor"].endswith(cid) and c["facts"], cid
        assert all(f["confidence"] == "vendor_doc" for f in c["facts"]), cid
        if c.get("add_on"):
            assert c["confirmations"], cid                       # the user checks the pins on the real part
            assert all(c["pins"][s]["kind"] in ("digital_out", "analog_out") for s in c["add_on"]["signals"]), cid
        else:
            assert len(c["not_buildable"]) > 20 and not c["pins"], cid
    assert set(not_buildable()) == set(KIT) - set(ADDABLE) - {"ky-037"}


@pytest.mark.parametrize("cid", ADDABLE)
def test_an_addable_part_gets_a_safe_plan_on_free_pins(cid):
    cat = facts.catalog()
    spec, steps, new = level_meter_plus(cid)
    assert facts.check_safety(steps + new) == []
    targets = [s.connection.to_pin.pin for s in new]
    signals = cat[cid]["add_on"]["signals"]
    assert targets[:2] == ["GND", "3V3"] and len(targets) == 2 + len(signals)
    assert "A0" not in targets and len(set(targets)) == len(targets)        # the microphone keeps A0
    assert "Unplug USB" in new[0].display_text and "Plug USB in" in new[-1].display_text
    ctx = codegen.build_context(spec, steps + new)
    added = [i for i in ctx.inputs if i.component == cid]
    assert [i.kind for i in added] == ["analog" if cat[cid]["pins"][s]["kind"] == "analog_out" else "digital"
                                       for s in signals]
    assert all(any(cat[cid]["pins"][s]["fact"] == f["id"] and f["statement"] in ctx.facts for f in cat[cid]["facts"])
               for s in signals)                                             # the generator gets the vendor's facts


def test_unmarked_pins_are_described_and_several_signals_are_told_apart():
    _, steps, new = level_meter_plus("ky-004")
    assert [s.display_text for s in new] == ["Unplug USB. KY-004 minus pin to board GND",
                                             "KY-004 middle pin to board +3V3 (shared)",
                                             "KY-004 S to board D2. Plug USB in"]
    assert "the one between S and minus" in new[1].spoken_text and "labelled" not in new[1].spoken_text
    spec, steps, new = level_meter_plus("ky-023")
    assert [s.connection.to_pin.pin for s in new] == ["GND", "3V3", "A1", "A2"]      # the button stays unconnected
    names = [i.name for i in codegen.build_context(spec, steps + new).inputs]
    assert names[1:] == ["KY-023 joystick, its VRx signal", "KY-023 joystick, its VRy signal"]
    assert "analogRead(A2) only" in codegen.task_prompt(codegen.build_context(spec, steps + new))
    spec, steps, new = level_meter_plus("ky-040")
    task = codegen.task_prompt(codegen.build_context(spec, steps + new))
    assert "its SW signal, digital output on header D7" in task and "It reads LOW while the sensor reports its event" in task
    assert "No level is documented for it" in task                                   # CLK and DT


def test_a_part_without_wiring_rules_is_never_planned():
    for cid in not_buildable():
        with pytest.raises(ValueError):
            planner.plan_extension(planner.plan_wiring(planner.level_meter_spec()), cid)


def test_the_board_runs_out_of_inputs_instead_of_sharing_one():
    steps = planner.plan_wiring(planner.level_meter_spec())
    for cid in ("ky-040", "ky-004"):                # 3 + 1 digital signals: all four preferred pins
        steps += planner.plan_extension(steps, cid)
    assert facts.check_safety(steps) == []
    with pytest.raises(ValueError, match="no free digital input"):
        planner.plan_extension(steps, "ky-017")


def test_asking_for_a_part_that_cannot_be_built_gives_the_databases_reason():
    sid = generated_level_meter()
    r = client.post(f"/sessions/{sid}/extend", json={"instruction": "add the relay"}).json()
    assert r["extension_problem"] == facts.catalog()["ky-019"]["not_buildable"] and r["state"] == "code_generated"
    # Even if the model proposes such a part, the route refuses it with the same reason.
    example.use_fake_ai({"kind": "add_part", "component_id": "ky-050", "behavior": "show the distance", "reason": "",
                         "needs_purpose": False, "ideas": []})
    r = client.post(f"/sessions/{sid}/extend", json={"instruction": "add the ultrasonic sensor"}).json()
    assert r["extension_problem"] == facts.catalog()["ky-050"]["not_buildable"]
    assert r["project"]["components"] == ["uno-q", "ky-037"]


def test_claude_is_told_what_can_be_added_and_what_cannot():
    sid = generated_level_meter()
    fake = example.use_fake_ai({"kind": "add_part", "component_id": "ky-018", "reason": "", "needs_purpose": False,
                                "behavior": "Show the sound level only while it is dark.", "ideas": []})
    example.use_fake_codegen()                      # a code generator is available, so the part can be built
    s = client.post(f"/sessions/{sid}/extend", json={"instruction": "add the light sensor, meter only in the dark"}).json()
    req = fake.requests[0]
    text = req["messages"][0]["content"][0]["text"]
    assert "ky-018 (KY-018 photoresistor): senses brightness" in text
    assert "KY-019 relay: It needs 5 volts" in text
    assert req["output_config"]["format"]["schema"]["properties"]["component_id"]["enum"] == \
        addable_sensors(planner.level_meter_spec()) + ["none"]
    assert s["state"] == "needs_confirmation" and s["project"]["components"][-1] == "ky-018"
    assert [p["id"] for p in s["pending_confirmations"]] == ["ky-018-present", "ky018-pin-labels"]
    s = client.post(f"/sessions/{sid}/analyze", data={
        "confirmed_inventory": '[{"component_id": "ky-018", "confirmations": ["ky018-pin-labels"]}]'}).json()
    assert s["state"] == "wiring" and "KY-018 minus pin to board GND" in s["wiring_steps"][s["current_step"]]["display_text"]
    assert s["wiring_steps"][-1]["connection"]["to_pin"]["pin"] == "A1"


def test_without_ai_a_named_part_is_found_and_its_purpose_asked():
    spec = planner.level_meter_spec()
    assert keyword_extension("add the KY-004", spec).component_id == "ky-004"
    p = keyword_extension("please add the knock sensor", spec)
    assert (p.kind, p.component_id, p.needs_purpose) == ("add_part", "ky-031", True)
    assert keyword_extension("add a ky 15", spec).kind == "unsupported"
    assert keyword_extension("make the bar blink when it is loud", spec).kind == "change_behavior"
