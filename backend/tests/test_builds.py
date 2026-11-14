"""Build-assistant flow against the API, offline (mock planner, mock deploy agent)."""
import io
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.build import facts
from app.build.models import Connection, PinRef, WiringStep
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import export_schemas  # noqa: E402
import learnlens_agent  # noqa: E402

client = TestClient(app)


def agent_post(path, payload):
    r = client.post(path, json=payload)
    return r.status_code, (r.json() if r.content else None)


def new_build():
    r = client.post("/builds", json={"goal": "I want to build a clap-activated light."})
    assert r.status_code == 200, r.text
    return r.json()


def to_approval():
    b = new_build()
    return client.post(f"/builds/{b['build_id']}/wiring/confirm").json()


def test_clap_light_end_to_end():
    b = new_build()
    bid = b["build_id"]
    assert b["status"] == "wiring" and b["provenance"] == "mock" and b["sketch"] is None
    assert {"ky-037", "uno-q"} == set(b["components"])
    assert all(f["source"] and f["confidence"] for f in b["hardware_facts"])

    b = client.post(f"/builds/{bid}/wiring/confirm").json()
    assert b["status"] == "awaiting_approval" and "SENSOR_PIN = 2;" in b["sketch"]["code"]

    b = client.post(f"/builds/{bid}/approve", json={"sketch_sha256": b["sketch"]["sha256"]}).json()
    assert b["status"] == "approved"

    b = learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent())
    assert b["build_id"] == bid and b["status"] == "awaiting_test" and b["deploy"]["mock"] is True

    b = client.post(f"/builds/{bid}/test-result", json={"passed": True}).json()
    assert b["status"] == "done" and b["test_passed"] is True


def test_build_from_photo_upload():
    jpeg = ("desk.jpg", io.BytesIO(b"\xff\xd8\xff\xe0" + b"0" * 100), "image/jpeg")
    r = client.post("/builds/photo", data={"goal": "clap light"}, files={"image": jpeg})
    assert r.status_code == 200, r.text
    assert r.json()["photo_received"] is True and r.json()["status"] == "wiring"
    assert new_build()["photo_received"] is False

    heic = ("desk.heic", io.BytesIO(b"x"), "image/heic")
    assert client.post("/builds/photo", data={"goal": "g"}, files={"image": heic}).status_code == 415
    big = ("big.jpg", io.BytesIO(b"0" * (5 * 1024 * 1024 + 1)), "image/jpeg")
    assert client.post("/builds/photo", data={"goal": "g"}, files={"image": big}).status_code == 413
    assert client.post("/builds/photo", data={"goal": "g"}).status_code == 422


def test_nothing_to_flash_without_approval():
    b = to_approval()
    assert learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent()) is None
    assert client.get(f"/builds/{b['build_id']}").json()["status"] == "awaiting_approval"


def test_approval_must_match_sketch_hash():
    b = to_approval()
    r = client.post(f"/builds/{b['build_id']}/approve", json={"sketch_sha256": "0" * 64})
    assert r.status_code == 409
    assert learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent()) is None


def test_cannot_approve_before_wiring_confirmed_or_after_reject():
    b = new_build()
    assert client.post(f"/builds/{b['build_id']}/approve", json={"sketch_sha256": "x"}).status_code == 409
    b = to_approval()
    assert client.post(f"/builds/{b['build_id']}/reject").json()["status"] == "rejected"
    r = client.post(f"/builds/{b['build_id']}/approve", json={"sketch_sha256": b["sketch"]["sha256"]})
    assert r.status_code == 409


def test_agent_refuses_tampered_sketch():
    b = to_approval()
    client.post(f"/builds/{b['build_id']}/approve", json={"sketch_sha256": b["sketch"]["sha256"]})

    def post(path, payload):
        status, body = agent_post(path, payload)
        if path.endswith("/claim"):
            body["sketch"]["code"] += "\n// injected"
        return status, body

    out = learnlens_agent.run_once(post, learnlens_agent.mock_agent())
    assert out["status"] == "failed" and out["deploy"]["upload_ok"] is False
    assert out["deploy"]["failed_stage"] == "validate" and "sha256" in out["deploy"]["log"]


def test_approval_token_is_bound_to_build_and_retry_needs_a_new_approval():
    b = to_approval()
    bid, sha = b["build_id"], b["sketch"]["sha256"]
    client.post(f"/builds/{bid}/approve", json={"sketch_sha256": sha})

    def post_without_token(path, payload):
        status, body = agent_post(path, payload)
        if path.endswith("/claim"):
            body["approval_token"] = ""
        return status, body

    out = learnlens_agent.run_once(post_without_token, learnlens_agent.mock_agent())
    assert out["status"] == "failed" and out["deploy"]["failed_stage"] == "approval"
    assert out["deploy"]["compile_ok"] is True and out["deploy"]["upload_ok"] is False
    assert [e["stage"] for e in out["deploy_events"]] == ["validate", "compile", "compile", "approval"]

    # Retry: the user approves again, which signs a fresh token and clears the old result.
    again = client.post(f"/builds/{bid}/approve", json={"sketch_sha256": sha}).json()
    assert again["status"] == "approved" and again["deploy"] is None and again["deploy_events"] == []
    out = learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent())
    assert out["status"] == "awaiting_test" and out["deploy"]["mock"] is True
    assert "SIMULATED" in out["deploy"]["log"]
    assert out["deploy_events"][-1]["stage"] == "upload"

    # A failed behavior test is not a deploy failure: it cannot be re-approved.
    client.post(f"/builds/{bid}/test-result", json={"passed": False})
    assert client.post(f"/builds/{bid}/approve", json={"sketch_sha256": sha}).status_code == 409


def test_agent_token_required_when_configured(monkeypatch):
    monkeypatch.setenv("LEARNLENS_AGENT_TOKEN", "s3cret")
    assert client.post("/agent/jobs/claim", json={"agent_id": "a"}).status_code == 401
    r = client.post("/agent/jobs/claim", json={"agent_id": "a"}, headers={"X-Agent-Token": "s3cret"})
    assert r.status_code in (200, 204)


def test_failed_behavior_test_and_unknown_build():
    b = to_approval()
    bid = b["build_id"]
    client.post(f"/builds/{bid}/approve", json={"sketch_sha256": b["sketch"]["sha256"]})
    learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent())
    b = client.post(f"/builds/{bid}/test-result", json={"passed": False, "note": "LED stays off"}).json()
    assert b["status"] == "failed" and b["error"] == "LED stays off"
    assert client.get("/builds/nope").status_code == 404


@pytest.mark.parametrize("component,pin,fact,expected", [
    ("uno-q", "D99", [], "uno-q has no pin D99"),
    ("esp32", "D2", [], "unknown component esp32"),
    ("uno-q", "D2", ["made-up-fact"], "unknown fact made-up-fact"),
])
def test_plan_validation_rejects_invented_hardware(component, pin, fact, expected):
    step = WiringStep(index=1, display_text="x", fact_ids=fact, connection=Connection(
        from_pin=PinRef(component="ky-037", pin="DO"), to_pin=PinRef(component=component, pin=pin)))
    assert facts.validate_plan([step]) == [f"step 1: {expected}"]


def test_shared_schema_file_is_current():
    assert export_schemas.OUT.read_text() == export_schemas.render(), \
        "run: .venv/bin/python scripts/export_schemas.py"
