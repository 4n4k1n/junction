"""Build-assistant API.

POST /builds                          goal -> wiring plan + hardware facts
POST /builds/photo                    multipart goal + image -> same (photo of the parts on the desk)
GET  /builds/{id}                     current state
POST /builds/{id}/wiring/confirm      wiring done -> sketch generated, awaiting approval
POST /builds/{id}/approve             explicit approval of one exact sketch (sha256) -> deploy job
POST /builds/{id}/reject              user declines the sketch
POST /builds/{id}/test-result         user reports whether the behavior works
POST /agent/jobs/claim                Mac deploy agent: next approved job (204 if none)
POST /agent/jobs/{job_id}/events      Mac deploy agent: live status while it compiles and uploads
POST /agent/jobs/{job_id}/result      Mac deploy agent: final compile/upload result

A deploy job is created only by /approve, which also signs the short-lived approval token the
agent needs to upload. Nothing else can make the agent flash. Approving again after a failed
deploy is the retry.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Dict, Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Response, UploadFile

from . import approval, facts
from .models import (
    ApproveRequest, Build, BuildStatus, ClaimRequest, CreateBuildRequest, DeployEvent, DeployJob, DeployResult,
    TestResultRequest,
)
from .planner import MockPlanner

router = APIRouter()
planner = MockPlanner()

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/webp", "image/gif"}

# In-memory for the scaffold: builds and jobs are lost on restart.
builds: Dict[str, Build] = {}
jobs: Dict[str, DeployJob] = {}
agent_seen = {"at": 0.0, "mock": False}  # last poll from a deploy agent


def agent_status() -> dict:
    return {"agent_connected": time.time() - agent_seen["at"] < 10, "agent_mock": agent_seen["mock"]}


def _load(build_id: str, *allowed: BuildStatus) -> Build:
    b = builds.get(build_id)
    if b is None:
        raise HTTPException(404, "build not found")
    if allowed and b.status not in allowed:
        raise HTTPException(409, f"build is {b.status.value}")
    return b


def _check_agent(token: Optional[str]) -> None:
    expected = os.getenv("LEARNLENS_AGENT_TOKEN")
    if expected and token != expected:
        raise HTTPException(401, "bad agent token")


def _create(goal: str, image: Optional[bytes] = None) -> Build:
    components, steps = planner.plan(goal, image)
    problems = facts.check_safety(steps)
    if problems:
        raise HTTPException(502, {"error": "plan contradicts hardware facts", "problems": problems})
    b = Build(build_id=uuid.uuid4().hex[:12], goal=goal, status=BuildStatus.wiring,
              provenance=planner.provenance, components=components,
              hardware_facts=facts.facts_for(components), wiring_steps=steps,
              photo_received=image is not None)
    builds[b.build_id] = b
    return b


def create_awaiting_approval(goal: str, components, steps, sketch, provenance) -> Build:
    """A build whose wiring was already confirmed step by step in a guided session."""
    b = Build(build_id=uuid.uuid4().hex[:12], goal=goal, status=BuildStatus.awaiting_approval,
              provenance=provenance, components=components, hardware_facts=facts.facts_for(components),
              wiring_steps=steps, sketch=sketch)
    builds[b.build_id] = b
    return b


@router.post("/builds", response_model=Build)
def create_build(req: CreateBuildRequest):
    return _create(req.goal)


@router.post("/builds/photo", response_model=Build)
async def create_build_with_photo(goal: str = Form(...), image: UploadFile = File(...)):
    media_type = (image.content_type or "").lower()
    if media_type not in ALLOWED_MEDIA:
        raise HTTPException(415, f"unsupported image type {media_type}")
    data = await image.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image larger than 5 MB – downscale on the phone")
    return _create(goal, data)


@router.get("/builds/{build_id}", response_model=Build)
def get_build(build_id: str):
    return _load(build_id)


@router.post("/builds/{build_id}/wiring/confirm", response_model=Build)
def confirm_wiring(build_id: str):
    b = _load(build_id, BuildStatus.wiring)
    b.sketch = planner.sketch(b.goal, b.wiring_steps)
    b.status = BuildStatus.awaiting_approval
    return b


@router.post("/builds/{build_id}/approve", response_model=Build)
def approve(build_id: str, req: ApproveRequest):
    b = _load(build_id, BuildStatus.awaiting_approval, BuildStatus.failed)
    # A failed deploy can be approved again (retry). A failed behavior test cannot: that needs a new sketch.
    if b.status == BuildStatus.failed and (b.deploy is None or b.test_passed is not None):
        raise HTTPException(409, "build is failed")
    if b.sketch is None or req.sketch_sha256 != b.sketch.sha256:
        raise HTTPException(409, "approved hash does not match the current sketch")
    for stale in [j for j in jobs.values() if j.build_id == b.build_id]:
        del jobs[stale.job_id]
    job = DeployJob(job_id=uuid.uuid4().hex[:12], build_id=b.build_id, sketch=b.sketch,
                    fqbn=facts.fqbn("uno-q"), approval_token=approval.mint(b.build_id, b.sketch.sha256),
                    expected_serial_events=b.expected_serial_events)
    jobs[job.job_id] = job
    b.status, b.deploy, b.deploy_events, b.error = BuildStatus.approved, None, [], ""
    return b


@router.post("/builds/{build_id}/reject", response_model=Build)
def reject(build_id: str):
    b = _load(build_id, BuildStatus.awaiting_approval)
    b.status = BuildStatus.rejected
    return b


@router.post("/builds/{build_id}/test-result", response_model=Build)
def test_result(build_id: str, req: TestResultRequest):
    b = _load(build_id, BuildStatus.awaiting_test)
    b.test_passed = req.passed
    b.status = BuildStatus.done if req.passed else BuildStatus.failed
    b.error = "" if req.passed else (req.note or "behavior test failed")
    return b


@router.post("/agent/jobs/claim", response_model=DeployJob, responses={204: {"description": "no job"}})
def claim_job(req: ClaimRequest, x_agent_token: Optional[str] = Header(None)):
    _check_agent(x_agent_token)
    agent_seen.update(at=time.time(), mock=req.mock)
    for job in jobs.values():
        b = builds[job.build_id]
        if b.status == BuildStatus.approved:
            b.status = BuildStatus.deploying
            return job
    return Response(status_code=204)


@router.post("/agent/jobs/{job_id}/events", response_model=Build)
def job_event(job_id: str, event: DeployEvent, x_agent_token: Optional[str] = Header(None)):
    _check_agent(x_agent_token)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    b = _load(job.build_id, BuildStatus.deploying, BuildStatus.awaiting_test)
    if event.stage == "runtime" and b.deploy is not None:
        # A line the agent read from the board after the upload (while the user tests).
        b.deploy.runtime_log = (b.deploy.runtime_log + [event.message])[-100:]
        b.deploy.observed_events = [e for e in b.expected_serial_events
                                    if any(e in line for line in b.deploy.runtime_log)]
    else:
        b.deploy_events = (b.deploy_events + [event])[-50:]
    return b


@router.post("/agent/jobs/{job_id}/result", response_model=Build)
def job_result(job_id: str, result: DeployResult, x_agent_token: Optional[str] = Header(None)):
    _check_agent(x_agent_token)
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "job not found")
    b = _load(job.build_id, BuildStatus.deploying)
    b.deploy = result
    ok = result.compile_ok and result.upload_ok
    b.status = BuildStatus.awaiting_test if ok else BuildStatus.failed
    b.error = "" if ok else (f"{result.failed_stage or ('compile' if not result.compile_ok else 'upload')} failed")
    return b
