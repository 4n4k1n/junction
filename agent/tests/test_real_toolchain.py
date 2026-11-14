"""Opt-in: backend + agent with the real arduino-cli and UNO Q core, and NO board attached.

    LEARNLENS_REAL_COMPILE=1 backend/.venv/bin/python -m pytest agent/tests/test_real_toolchain.py

Skipped when a board is connected, so a test run can never flash anything.
"""
import os

import pytest

from lldeploy.core import DeployAgent

pytestmark = pytest.mark.skipif(os.getenv("LEARNLENS_REAL_COMPILE") != "1", reason="set LEARNLENS_REAL_COMPILE=1")


def test_approved_job_compiles_for_real_and_stops_at_the_missing_board(monkeypatch):
    monkeypatch.delenv("LEARNLENS_ARDUINO_CLI", raising=False)
    agent = DeployAgent()
    try:
        device = agent.device()
        if not device["toolchain"]["ready"]:
            pytest.skip(device["problem"])
        if device["connected"]:
            pytest.skip("a board is connected: this test must not flash it")

        from fastapi.testclient import TestClient

        import learnlens_agent
        from app.main import app

        client = TestClient(app)
        inventory = [{"component_id": "uno-q"}, {"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]
        sid = client.post("/sessions", json={"instruction": "Build a clap-activated light",
                                             "confirmed_inventory": inventory}).json()["session_id"]
        s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
        for i in range(len(s["wiring_steps"])):
            client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
        g = client.post(f"/sessions/{sid}/generate").json()
        client.post(f"/builds/{g['build_id']}/approve", json={"sketch_sha256": g["sketch"]["sha256"]})

        def post(path, payload):
            r = client.post(path, json=payload)
            return r.status_code, (r.json() if r.content else None)

        build = None
        for _ in range(20):  # other tests may have left approved jobs in the queue
            build = learnlens_agent.run_once(post, agent)
            if build is None or build["build_id"] == g["build_id"]:
                break
        assert build and build["status"] == "failed" and build["error"] == "device failed"
        assert build["deploy"]["compile_ok"] is True and build["deploy"]["upload_ok"] is False
        assert build["deploy"]["mock"] is False and "no Arduino UNO Q found" in build["deploy"]["log"]
        assert [(e["stage"], e["status"]) for e in build["deploy_events"]] == [
            ("validate", "ok"), ("compile", "started"), ("compile", "ok"), ("approval", "ok"), ("device", "failed")]
    finally:
        agent.close()
