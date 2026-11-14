"""Shorter waits: code written during wiring, compile before approval, state kept across a restart."""
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.build import codegen, persistence, router, session_router
from app.build.analyzer import Analyzer
from app.build.models import CompileReport
from app.main import app

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent"))
import example_clap_session as example  # noqa: E402
import learnlens_agent  # noqa: E402

client = TestClient(app)
INVENTORY = [{"component_id": "uno-q"}, {"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]


class OkCompiler:
    def compile(self, sketch_dir, fqbn):
        return CompileReport(status="ok", fqbn=fqbn, log="Sketch uses 87032 bytes")


def agent_post(path, payload):
    r = client.post(path, json=payload)
    return r.status_code, (r.json() if r.content else None)


def start_wiring(goal="Build a clap-activated light"):
    sid = client.post("/sessions", json={"instruction": goal, "confirmed_inventory": INVENTORY}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
    assert s["state"] == "wiring", s
    return sid, s


def finish_wiring(sid, s):
    for i in range(len(s["wiring_steps"])):
        client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})


def test_the_sketch_is_written_while_the_user_is_still_wiring(monkeypatch):
    monkeypatch.setattr(session_router, "PREGENERATE", True)
    session_router.analyzer = Analyzer(mode="mock")
    fake = example.use_fake_codegen(example.CANNED_SKETCH, example.CANNED_SKETCH, compiler=OkCompiler())

    sid, s = start_wiring()
    session_router.pregenerated[sid][2].result(timeout=10)             # started when wiring began; wait for it
    assert len(fake.requests) == 1                                     # before a single wire was confirmed
    progress = client.get(f"/sessions/{sid}/status").json()["generation_progress"]
    assert progress[0] == "Claude is writing the code" and "Compiling for the Arduino UNO Q" in progress

    finish_wiring(sid, s)
    g = client.post(f"/sessions/{sid}/generate").json()
    assert g["sketch"]["code"] == example.CANNED_SKETCH["source_code"] and g["compile"]["status"] == "ok"
    assert len(fake.requests) == 1                                     # /generate reused it: no second model call
    assert client.get(f"/builds/{g['build_id']}").json()["status"] == "awaiting_approval"   # still needs approval

    # Different parameters than the early sketch was written for: generate again.
    sid, s = start_wiring()
    session_router.pregenerated[sid][2].result(timeout=10)
    finish_wiring(sid, s)
    fake.queue.append(dict(example.CANNED_SKETCH, source_code=example.CANNED_SKETCH["source_code"].replace(
        "DEBOUNCE_MS = 300", "DEBOUNCE_MS = 150")))
    g = client.post(f"/sessions/{sid}/generate", json={"debounce_ms": 150}).json()
    assert "DEBOUNCE_MS = 150" in g["sketch"]["code"] and len(fake.requests) == 3


def test_the_agent_compiles_before_approval_and_only_uploads_after_it():
    session_router.analyzer = Analyzer(mode="mock")
    session_router.generator = codegen.CodeGenerator(mode="mock")
    while learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent()):
        pass                                                           # drain deploy jobs left by other tests
    router.prepare_jobs.clear()

    sid, s = start_wiring()
    finish_wiring(sid, s)
    g = client.post(f"/sessions/{sid}/generate").json()
    bid = g["build_id"]
    agent = learnlens_agent.mock_agent()

    assert learnlens_agent.run_once(agent_post, agent) is None        # the compile-only job: nothing to report back
    build = client.get(f"/builds/{bid}").json()
    assert build["precompiled"] is True and build["status"] == "awaiting_approval" and build["deploy"] is None
    assert agent.has_compiled(bid, g["sketch"]) and not agent.builds[bid].get("uploaded")
    assert learnlens_agent.run_once(agent_post, agent) is None        # offered once; still nothing to flash

    client.post(f"/builds/{bid}/approve", json={"sketch_sha256": g["sketch"]["sha256"]})
    build = learnlens_agent.run_once(agent_post, agent)
    assert build["status"] == "awaiting_test"
    assert [(e["stage"], e["message"]) for e in build["deploy_events"]][0] == (
        "compile", "already compiled while waiting for approval")      # no second compile after the approval
    assert [e["stage"] for e in build["deploy_events"]] == ["compile", "approval", "upload"]

    # A changed sketch is never treated as already compiled.
    tampered = dict(g["sketch"], code=g["sketch"]["code"] + "\n// changed")
    assert not agent.has_compiled(bid, tampered)


def test_sessions_and_builds_survive_a_backend_restart(monkeypatch, tmp_path):
    state_file = tmp_path / "state.json"
    monkeypatch.setenv("LEARNLENS_STATE_FILE", str(state_file))
    session_router.analyzer = Analyzer(mode="mock")
    session_router.generator = codegen.CodeGenerator(mode="mock")

    sid, s = start_wiring()
    client.post(f"/sessions/{sid}/confirm-step", json={"step_index": 0})
    assert sid in json.loads(state_file.read_text())["sessions"]       # written after every change

    before = client.get(f"/sessions/{sid}/status").json()
    session_router.sessions.clear()                                    # "restart"
    assert client.get(f"/sessions/{sid}/status").status_code == 404
    persistence.load()
    assert client.get(f"/sessions/{sid}/status").json() == before
    assert client.post(f"/sessions/{sid}/confirm-step", json={"step_index": 1}).json()["current_step"] == 2

    state_file.write_text("{ not json")                                # a damaged file does not stop the backend
    persistence.load()
