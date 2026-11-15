"""Running without AI, health reporting and runtime-log events."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import codecheck, codegen, session_router
from app.build.analyzer import Analyzer
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
import learnlens_agent  # noqa: E402

client = TestClient(app)


def agent_post(path, payload):
    r = client.post(path, json=payload)
    return r.status_code, (r.json() if r.content else None)


@pytest.fixture(autouse=True)
def offline():
    session_router.analyzer = Analyzer(mode="mock")
    session_router.generator = codegen.CodeGenerator(mode="mock")


def generated():
    """A clap light written from the template, without AI: waiting for approval."""
    sid = client.post("/sessions", json={"instruction": "I want to build a clap-activated light."}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze").json()                  # no photo needed: the parts are asked for
    assert s["state"] == "needs_confirmation" and s["analysis_source"] == "mock"
    inventory = '[{"component_id": "uno-q"}, {"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]'
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": inventory}).json()
    for i in range(len(s["wiring_steps"])):
        client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    return client.post(f"/sessions/{sid}/generate").json()


def test_without_ai_the_template_is_used_labelled_and_still_needs_approval():
    health = client.get("/health").json()
    assert "demo_mode" not in health and health["analyzer"] == "mock" and health["codegen"] == "mock"
    assert health["ai_available"] is False                              # the app says so: prevalidated projects only

    first, second = generated(), generated()
    assert first["source"] == "template" and first["sketch"]["provenance"] == "rules"
    assert first["sketch"]["code"] == second["sketch"]["code"]          # same every time
    assert first["hardware_verified"] is False and first["flashed"] is False
    ctx = codegen.build_context(session_router.planner.clap_light_spec(),
                                session_router.planner.plan_wiring(session_router.planner.clap_light_spec()))
    assert codecheck.check_sketch(first["sketch"]["code"], ctx) == []   # prevalidated, and checked again

    while learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent()):
        pass                                                              # drain jobs left by other tests
    for g in (first, second):
        assert client.get(f"/builds/{g['build_id']}").json()["status"] == "awaiting_approval"


def test_health_reports_whether_an_agent_is_polling_and_if_it_is_a_mock():
    learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent())
    health = client.get("/health").json()
    assert health["agent_connected"] is True and health["agent_mock"] is True


def test_runtime_lines_from_the_board_reach_the_build_while_the_user_tests():
    g = generated()
    bid = g["build_id"]
    client.post(f"/builds/{bid}/approve", json={"sketch_sha256": g["sketch"]["sha256"]})
    build = None
    for _ in range(20):
        build = learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent())
        if build is None or build["build_id"] == bid:
            break
    assert build["status"] == "awaiting_test" and build["deploy"]["runtime_log"] == []
    job_id = build["_job"]["job_id"]

    for line in ("READY", "SOUND_DETECTED", "LED_ON"):
        r = client.post(f"/agent/jobs/{job_id}/events", json={"stage": "runtime", "status": "ok", "message": line})
        assert r.status_code == 200
    deploy = client.get(f"/builds/{bid}").json()["deploy"]
    assert deploy["runtime_log"] == ["READY", "SOUND_DETECTED", "LED_ON"]
    assert deploy["observed_events"] == ["READY", "SOUND_DETECTED", "LED_ON"]   # LED_OFF not seen, not claimed
