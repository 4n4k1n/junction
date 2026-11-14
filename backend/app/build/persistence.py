"""Keeps sessions, builds and jobs across a backend restart.

After every change the whole state is written to one JSON file (it is small). Sketches that were
being written in the background are not kept: they are generated again when asked for.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger("learnlens.state")


def _path():
    value = os.getenv("LEARNLENS_STATE_FILE", str(Path(__file__).resolve().parents[2] / "learnlens_state.json"))
    return None if value in ("", "none") else Path(value)


def save() -> None:
    from . import router, session_router

    path = _path()
    if path is None:
        return
    state = {
        "sessions": {k: v.model_dump(mode="json") for k, v in session_router.sessions.items()},
        "running_code": session_router.running_code,
        "builds": {k: v.model_dump(mode="json") for k, v in router.builds.items()},
        "jobs": {k: v.model_dump(mode="json") for k, v in router.jobs.items()},
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)  # atomic: a crash never leaves half a file


def load() -> None:
    from . import router, session_router
    from .models import Build, DeployJob, GuideStatus

    path = _path()
    if path is None or not path.is_file():
        return
    try:
        state = json.loads(path.read_text())
        session_router.sessions.update({k: GuideStatus.model_validate(v) for k, v in state["sessions"].items()})
        session_router.running_code.update(state.get("running_code", {}))
        router.builds.update({k: Build.model_validate(v) for k, v in state["builds"].items()})
        router.jobs.update({k: DeployJob.model_validate(v) for k, v in state.get("jobs", {}).items()})
        log.info("restored %d session(s) and %d build(s)", len(session_router.sessions), len(router.builds))
    except Exception as e:  # an old or damaged file must not stop the backend
        log.warning("could not restore state from %s: %s", path, e)
