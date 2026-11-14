"""LearnLens Mac deploy agent.

    python3 agent/learnlens_agent.py                      # real toolchain, real board
    python3 agent/learnlens_agent.py --mock               # no tools, no board: every result says SIMULATED
    python3 agent/learnlens_agent.py --no-backend         # local API only

It does two things:
  1. Serves a small API on http://127.0.0.1:8765 (GET /device, POST /compile, POST /flash, GET /logs).
  2. Polls the LearnLens backend for approved jobs, runs them through the same pipeline and
     reports each stage back, so the app can show live status.

An upload happens only with the approval token the backend signs when the user approves a sketch.
Standard library only, so it runs with the system python3. See agent/README.md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lldeploy.core import DeployAgent, Rejected  # noqa: E402
from lldeploy.server import HOST, make_server  # noqa: E402

Post = Callable[[str, dict], Tuple[int, Optional[dict]]]


def http_post(backend: str, token: Optional[str]) -> Post:
    def post(path: str, payload: dict) -> Tuple[int, Optional[dict]]:
        req = urllib.request.Request(backend.rstrip("/") + path, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json",
                                              **({"X-Agent-Token": token} if token else {})})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
            return r.status, (json.loads(body) if body else None)
    return post


def mock_agent() -> DeployAgent:
    return DeployAgent(mock=True)


def run_once(post: Post, agent: DeployAgent, agent_id: str = "mac-agent") -> Optional[dict]:
    """Claim and run at most one job. Returns the updated build, or None if there was no job."""
    status, job = post("/agent/jobs/claim", {"agent_id": agent_id, "mock": agent.mock})
    if status == 204 or job is None:
        return None
    job_id, build_id = job["job_id"], job["build_id"]
    # Stream each stage to the backend while the job runs.
    agent.on_event = lambda bid, event: post(f"/agent/jobs/{job_id}/events", event) if bid == build_id else None

    result = {"compile_ok": False, "upload_ok": False, "mock": agent.mock}
    try:
        compiled = agent.compile({"build_id": build_id, "sketch": job["sketch"], "fqbn": job.get("fqbn")})
        result.update(compile_ok=compiled["status"] == "ok", diagnostics=compiled["diagnostics"], log=compiled["log"])
        if result["compile_ok"]:
            flashed = agent.flash(build_id, job.get("approval_token") or "", job.get("expected_serial_events") or [])
            result.update(upload_ok=flashed["upload_ok"], log=flashed["log"], port=flashed["port"],
                          runtime_log=flashed["runtime_log"], observed_events=flashed["observed_events"])
        else:
            result["failed_stage"] = "compile"
    except Rejected as e:
        result.update(failed_stage=e.stage, log=str(e))
    except Exception as e:  # report instead of leaving the build stuck in "deploying"
        result.update(failed_stage=result["compile_ok"] and "upload" or "compile", log=f"{type(e).__name__}: {e}")
    finally:
        agent.on_event = None
    build = post(f"/agent/jobs/{job_id}/result", result)[1]
    if build is not None:
        build["_job"] = job  # for follow_logs
    return build


def follow_logs(post: Post, agent: DeployAgent, job: dict, seconds: float, chunk_s: float = 3) -> int:
    """After an upload, keep reading the board while the user tests it and send each new line to the
    backend. Stops early once every expected event was seen. Returns the number of lines sent."""
    build_id, expected = job["build_id"], job.get("expected_serial_events") or []
    sent = len(agent.logs(build_id)["runtime_log"])
    for line in agent.logs(build_id)["runtime_log"]:
        post(f"/agent/jobs/{job['job_id']}/events", {"stage": "runtime", "status": "ok", "message": line, "ts": time.time()})
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        lines = agent.logs(build_id, capture_s=chunk_s)["runtime_log"]
        for line in lines[sent:]:
            post(f"/agent/jobs/{job['job_id']}/events", {"stage": "runtime", "status": "ok", "message": line, "ts": time.time()})
        sent = len(lines)
        if expected and all(any(e in line for line in lines) for e in expected):
            break
    return sent


def poll_backend(agent: DeployAgent, backend: str, once: bool) -> None:
    post = http_post(backend, os.getenv("LEARNLENS_AGENT_TOKEN"))
    warned = False
    while True:
        try:
            build = run_once(post, agent)
            warned = False
        except (urllib.error.URLError, OSError) as e:
            build = None
            if not warned:
                print(f"backend not reachable at {backend}: {e}. Retrying.", flush=True)
                warned = True
        if build:
            print(f"build {build['build_id']}: {build['status']} {build['error']}", flush=True)
            if build["status"] == "awaiting_test" and not agent.mock:
                try:
                    n = follow_logs(post, agent, build["_job"], float(os.getenv("LEARNLENS_LOG_FOLLOW_S", "45")))
                    print(f"  {n} line(s) read from the board", flush=True)
                except (urllib.error.URLError, OSError, Rejected) as e:
                    print(f"  could not follow board output: {e}", flush=True)
        if once:
            return
        if not build:
            time.sleep(1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="http://localhost:8000")
    ap.add_argument("--port", type=int, default=int(os.getenv("LEARNLENS_AGENT_PORT", "8765")))
    ap.add_argument("--mock", action="store_true", help="simulate: run no tool and touch no hardware")
    ap.add_argument("--once", action="store_true", help="handle one backend job and exit")
    ap.add_argument("--no-backend", action="store_true", help="serve the local API only")
    args = ap.parse_args()

    agent = DeployAgent(mock=args.mock)
    try:
        if args.once:
            return poll_backend(agent, args.backend, once=True)
        server = make_server(agent, args.port)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        status = agent.device()
        print(f"LearnLens deploy agent on http://{HOST}:{args.port}  mock={args.mock}", flush=True)
        print(f"  arduino-cli: {status['toolchain']['arduino_cli']}  core: {status['toolchain']['core']}", flush=True)
        print(f"  board: {status['boards'] or status['problem']}", flush=True)
        if args.no_backend:
            threading.Event().wait()
        else:
            poll_backend(agent, args.backend, once=False)
    except KeyboardInterrupt:
        pass
    finally:
        agent.close()


if __name__ == "__main__":
    main()
