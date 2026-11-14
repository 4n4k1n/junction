"""Demo mode, health reporting and runtime-log events: the integration glue for the live demo."""
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import codecheck, codegen, demo, session_router
from app.build.analyzer import Analyzer
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
import learnlens_agent  # noqa: E402

client = TestClient(app)


def agent_post(path, payload):
    r = client.post(path, json=payload)
    return r.status_code, (r.json() if r.content else None)


@pytest.fixture
def demo_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("LEARNLENS_DEMO_MODE", "1")
    monkeypatch.setattr(demo, "SAVED_PHOTO", tmp_path / "desk.jpg")
    monkeypatch.setattr(session_router, "analyzer", Analyzer())
    monkeypatch.setattr(session_router, "generator", codegen.CodeGenerator())


def run_demo_flow():
    sid = client.post("/sessions", json={"instruction": "I want to build a clap-activated light."}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze").json()                  # no photo needed in demo mode
    assert s["state"] == "needs_confirmation" and s["analysis_source"] == "demo"
    assert [p["id"] for p in s["pending_confirmations"]] == ["ky-037-present", "ky037-pin-labels"]
    inventory = '[{"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]'
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": inventory}).json()
    for i in range(len(s["wiring_steps"])):
        client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    return client.post(f"/sessions/{sid}/generate").json()


def test_demo_mode_is_deterministic_labelled_and_uses_the_real_gates(demo_mode):
    health = client.get("/health").json()
    assert health["demo_mode"] is True and health["analyzer"] == "demo" and health["codegen"] == "demo"

    first, second = run_demo_flow(), run_demo_flow()
    assert first["source"] == "demo" and first["sketch"]["provenance"] == "rules"
    assert first["sketch"]["code"] == second["sketch"]["code"]          # same every time
    assert first["hardware_verified"] is False and first["flashed"] is False
    ctx = codegen.build_context(session_router.planner.clap_light_spec(),
                                session_router.planner.plan_wiring(session_router.planner.clap_light_spec()))
    assert codecheck.check_sketch(first["sketch"]["code"], ctx) == []   # prevalidated, and checked again

    # Demo mode does not weaken approval: nothing is deployable until the user approves.
    while learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent()):
        pass                                                              # drain jobs left by other tests
    for g in (first, second):
        assert client.get(f"/builds/{g['build_id']}").json()["status"] == "awaiting_approval"


def test_saved_photo_is_used_when_the_request_has_none(demo_mode):
    assert demo.saved_photo() == (None, "")
    demo.SAVED_PHOTO.write_bytes(b"\xff\xd8\xff\xe0saved")
    assert demo.saved_photo() == (b"\xff\xd8\xff\xe0saved", "image/jpeg")


def test_outside_demo_mode_nothing_is_labelled_demo():
    health = client.get("/health").json()
    assert health["demo_mode"] is False and health["analyzer"] == "mock"
    sid = client.post("/sessions", json={"instruction": "clap light"}).json()["session_id"]
    assert client.post(f"/sessions/{sid}/analyze").status_code == 422   # a photo or an inventory is required


def test_health_reports_whether_an_agent_is_polling_and_if_it_is_a_mock():
    learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent())
    health = client.get("/health").json()
    assert health["agent_connected"] is True and health["agent_mock"] is True


def test_runtime_lines_from_the_board_reach_the_build_while_the_user_tests(demo_mode):
    g = run_demo_flow()
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
