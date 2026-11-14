"""Guided session API: photo + instruction -> confirmed parts -> wiring, one confirmed step at a time.

POST /sessions                    instruction (+ optional confirmed inventory)
POST /sessions/{id}/analyze       multipart: image and/or confirmed_inventory (JSON list, "[]" = no photo, ask me)
                                  before wiring: detect parts, build the project spec and the plan
                                  during/after wiring: review a photo of the wiring (never a verification)
POST /sessions/{id}/confirm-step  the user confirms the current wiring step
POST /sessions/{id}/generate      write, check and compile the sketch; never uploads
GET  /sessions/{id}/status

State machine:
  awaiting_analysis -> needs_confirmation -> wiring -> wiring_complete -> code_generated (build awaiting approval)
                    -> unsupported | blocked (terminal: start a new session)

The plan is released only when every required part is either seen with high confidence or
confirmed by the user, every check that needs the physical part (pin labels) is confirmed, and
the plan passes facts.check_safety.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import TypeAdapter, ValidationError

from . import codegen, demo, facts, planner
from .analyzer import Analyzer, to_spec
from .models import (
    BuildStatus, ConfirmationKind, ConfirmStepRequest, CreateGuideSessionRequest, GenerateRequest,
    GenerationResult, GuideState, GuideStatus, InventoryItem, PendingConfirmation, Provenance, WiringReview,
)
from .router import ALLOWED_MEDIA, MAX_IMAGE_BYTES, builds, create_awaiting_approval

router = APIRouter()
analyzer = Analyzer()
generator = codegen.CodeGenerator()

CONFIDENT = float(os.getenv("LEARNLENS_DETECTION_THRESHOLD", "0.8"))
REVIEW_DISCLAIMER = ("A photo cannot verify an electrical connection. Check each wire against the steps "
                     "by hand before you plug in USB.")

# In-memory, like builds: sessions are lost on restart.
sessions: Dict[str, GuideStatus] = {}
_inventory = TypeAdapter(List[InventoryItem])


def _load(session_id: str, *allowed: GuideState) -> GuideStatus:
    s = sessions.get(session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    if allowed and s.state not in allowed:
        raise HTTPException(409, f"session is {s.state.value}")
    return s


def _say(s: GuideStatus, display: str, spoken: str = "") -> None:
    s.display_text, s.spoken_text = display, spoken or display


def _merge_inventory(s: GuideStatus, items: List[InventoryItem]) -> None:
    cat = facts.catalog()
    for item in items:
        if item.component_id not in cat:
            raise HTTPException(422, f"unknown component {item.component_id}")
        known = {c["id"] for c in cat[item.component_id].get("confirmations", [])}
        unknown = set(item.confirmations) - known
        if unknown:
            raise HTTPException(422, f"unknown confirmation {sorted(unknown)} for {item.component_id}")
        existing = next((i for i in s.confirmed_inventory if i.component_id == item.component_id), None)
        if existing is None:
            s.confirmed_inventory.append(item)
        else:
            existing.confirmations = sorted(set(existing.confirmations) | set(item.confirmations))


def _pending(s: GuideStatus) -> List[PendingConfirmation]:
    cat = facts.catalog()
    confirmed = {i.component_id: set(i.confirmations) for i in s.confirmed_inventory}
    pending = []
    for cid in s.project.components:
        name = cat[cid]["name"]
        if cid not in confirmed:
            best = max((d.confidence for d in s.detected_components if d.component_id == cid), default=None)
            if best is None:
                pending.append(PendingConfirmation(
                    id=f"{cid}-present", component_id=cid, kind=ConfirmationKind.presence,
                    question=f"I could not find the {name} in the photo. Do you have one on the desk?",
                    reason="Not detected."))
            elif best < CONFIDENT:
                pending.append(PendingConfirmation(
                    id=f"{cid}-present", component_id=cid, kind=ConfirmationKind.presence,
                    question=f"I think I see the {name}, but I am not sure. Is that what you have?",
                    reason=f"Detection confidence {best:.0%} is below {CONFIDENT:.0%}."))
        # Checks on the physical part are never satisfied by a photo.
        for c in cat[cid].get("confirmations", []):
            if c["id"] not in confirmed.get(cid, set()):
                pending.append(PendingConfirmation(id=c["id"], component_id=cid, kind=ConfirmationKind.variant,
                                                   question=c["question"], reason=c["reason"]))
    return pending


def _advance(s: GuideStatus) -> None:
    """Decide the state from what is known so far."""
    if not s.project.supported:
        s.state = GuideState.unsupported
        _say(s, "I can't build that yet", s.project.unsupported_reason)
        return
    s.hardware_facts = facts.facts_for(s.project.components)
    s.pending_confirmations = _pending(s)
    if s.pending_confirmations:
        s.state = GuideState.needs_confirmation
        first = s.pending_confirmations[0]
        _say(s, "Please confirm your parts", first.question)
        return
    steps = planner.plan_wiring(s.project)
    s.safety_problems = facts.check_safety(steps)
    if s.safety_problems:
        s.state = GuideState.blocked
        _say(s, "Stopped: wiring not safe", "I stopped because the wiring plan failed a safety check.")
        return
    s.wiring_steps, s.current_step, s.state = steps, 0, GuideState.wiring
    _say(s, steps[0].display_text, steps[0].spoken_text)


async def _read_image(image: Optional[UploadFile]):
    if image is None or not image.filename:
        return None, ""
    media_type = (image.content_type or "").lower()
    if media_type not in ALLOWED_MEDIA:
        raise HTTPException(415, f"unsupported image type {media_type}")
    data = await image.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "image larger than 5 MB – downscale on the phone")
    return data, media_type


@router.post("/sessions", response_model=GuideStatus)
def create_session(req: CreateGuideSessionRequest):
    s = GuideStatus(session_id=uuid.uuid4().hex[:12], state=GuideState.awaiting_analysis,
                    instruction=req.instruction.strip(), display_text="Look at your parts and take a photo",
                    spoken_text="Look at your parts and take a photo.")
    _merge_inventory(s, req.confirmed_inventory)
    sessions[s.session_id] = s
    return s


@router.post("/sessions/{session_id}/analyze", response_model=GuideStatus)
async def analyze(session_id: str, image: Optional[UploadFile] = File(None),
                  confirmed_inventory: Optional[str] = Form(None)):
    s = _load(session_id, GuideState.awaiting_analysis, GuideState.needs_confirmation,
              GuideState.wiring, GuideState.wiring_complete, GuideState.code_generated)
    data, media_type = await _read_image(image)
    if data is None and demo.enabled():
        data, media_type = demo.saved_photo()  # demo mode can start from the saved desk photo

    if s.state in (GuideState.wiring, GuideState.wiring_complete, GuideState.code_generated):
        if data is None:
            raise HTTPException(422, "send an image to review the wiring")
        findings, _ = analyzer.review(data, media_type, s.wiring_steps)
        s.review = WiringReview(observations=findings.observations, possible_issues=findings.possible_issues,
                                electrically_verified=False, disclaimer=REVIEW_DISCLAIMER)
        return s

    if data is None and confirmed_inventory is None and not demo.enabled():
        raise HTTPException(422, "send an image, a confirmed_inventory (\"[]\" for none), or both")
    if confirmed_inventory:
        try:
            items = _inventory.validate_python(json.loads(confirmed_inventory))
        except (json.JSONDecodeError, ValidationError) as e:
            raise HTTPException(422, f"confirmed_inventory is not a valid inventory list: {e}")
        _merge_inventory(s, items)

    # A new photo replaces the last detection. Without one, the first call still needs the project spec.
    if data is not None or s.project is None:
        analysis, s.analysis_source = analyzer.analyze(s.instruction, data, media_type or "image/jpeg")
        s.detected_components = analysis.components
        s.project = to_spec(analysis.project)
    _advance(s)
    return s


@router.post("/sessions/{session_id}/confirm-step", response_model=GuideStatus)
def confirm_step(session_id: str, req: ConfirmStepRequest):
    s = _load(session_id, GuideState.wiring)
    if req.step_index != s.current_step:
        raise HTTPException(409, f"current step is {s.current_step}, not {req.step_index}")
    step = s.wiring_steps[s.current_step]
    if not req.confirmed:
        _say(s, step.display_text, "No problem. " + step.spoken_text)
        return s

    s.confirmed_steps.append(s.current_step)
    if s.current_step + 1 < len(s.wiring_steps):
        s.current_step += 1
        nxt = s.wiring_steps[s.current_step]
        _say(s, nxt.display_text, nxt.spoken_text)
        return s

    s.current_step, s.state = None, GuideState.wiring_complete
    _say(s, "Wiring done. Writing the code", "Wiring is done. I am writing the code now.")
    return s


@router.post("/sessions/{session_id}/generate", response_model=GenerationResult)
def generate(session_id: str, req: Optional[GenerateRequest] = None):
    """Write the sketch for the confirmed plan. Ends with a build awaiting approval; nothing is uploaded."""
    s = _load(session_id, GuideState.wiring_complete, GuideState.code_generated)
    req = req or GenerateRequest()
    previous = builds.get(s.build_id) if s.build_id else None
    if previous is not None and previous.status != BuildStatus.awaiting_approval:
        raise HTTPException(409, f"the current sketch is already {previous.status.value}")
    problems = facts.check_safety(s.wiring_steps)
    if problems:
        raise HTTPException(409, {"error": "wiring plan is not valid", "problems": problems})

    ctx = codegen.build_context(s.project, s.wiring_steps, req.sensor_active_level, req.debounce_ms,
                                req.sample_window_ms)
    try:
        generated, source, attempts = generator.generate(s.session_id, ctx)
    except RuntimeError as e:
        raise HTTPException(502, str(e))

    if previous is not None:
        previous.status = BuildStatus.rejected  # replaced by the regenerated sketch
    provenance = Provenance.llm if source == "llm" else Provenance.rules
    sketch = planner.make_sketch(generated.source_code, provenance, filename=f"{codegen.SKETCH_NAME}.ino")
    build = create_awaiting_approval(s.instruction, s.project.components, s.wiring_steps, sketch, provenance)
    build.expected_serial_events = [e.event for e in generated.expected_serial_events]
    s.build_id, s.state = build.build_id, GuideState.code_generated
    _say(s, "Code ready. Review it on your phone", "The code is ready. Review and approve it on your phone.")
    return GenerationResult(
        session_id=s.session_id, build_id=build.build_id, source=source, sketch=sketch,
        explanation=generated.explanation, assumptions=generated.assumptions,
        expected_serial_events=generated.expected_serial_events,
        parameters=({"SAMPLE_WINDOW_MS": ctx.sample_window_ms, "sensor_pin": ctx.sensor_pin, "output": ctx.output,
                     "fqbn": ctx.fqbn} if ctx.signal_kind == "analog" else
                    {"SENSOR_ACTIVE_LEVEL": ctx.sensor_active_level, "DEBOUNCE_MS": ctx.debounce_ms,
                     "sensor_pin": ctx.sensor_pin, "led": ctx.led_symbol, "led_on_level": ctx.led_on_level,
                     "fqbn": ctx.fqbn}),
        attempts=attempts, repairs_used=sum(a.kind == "repair" for a in attempts),
        compile=codegen.final_compile(attempts, ctx.fqbn), project_dir=attempts[-1].project_dir)


@router.get("/sessions/{session_id}/status", response_model=GuideStatus)
def status(session_id: str):
    return _load(session_id)
