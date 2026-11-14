"""Guided session API: photo + instruction -> confirmed parts -> wiring, one confirmed step at a time.

POST /sessions                    instruction (+ optional confirmed inventory)
POST /sessions/{id}/analyze       multipart: image and/or confirmed_inventory (JSON list, "[]" = no photo, ask me)
                                  before wiring: detect parts, build the project spec and the plan
                                  during/after wiring: review a photo of the wiring (never a verification)
POST /sessions/{id}/confirm-step  the user confirms the current wiring step
POST /sessions/{id}/generate      write, check and compile the sketch; never uploads
POST /sessions/{id}/extend        develop a finished build further: add a known part or change what it does
POST /sessions/{id}/ask           a question about the build, answered from the plan and the verified facts
GET  /sessions/{id}/status

State machine:
  awaiting_analysis -> needs_confirmation -> wiring -> wiring_complete -> code_generated (build awaiting approval)
  code_generated -(extend: new part)-> needs_confirmation -> wiring (new steps only) -> wiring_complete -> ...
  code_generated -(extend: behaviour only)-> wiring_complete -> ...
                    -> unsupported | blocked (terminal: start a new session)

The plan is released only when every required part is either seen with high confidence or
confirmed by the user, every check that needs the physical part (pin labels) is confirmed, and
the plan passes facts.check_safety.
"""
from __future__ import annotations

import json
import os
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import TypeAdapter, ValidationError

from . import codegen, demo, facts, games, planner
from .analyzer import Analyzer, addable_sensors, to_spec
from .models import (
    AskRequest, StepAnswer, Behavior, BuildStatus, DetectedComponent, ConfirmationKind, ConfirmStepRequest, CreateGuideSessionRequest, ExtendRequest, GenerateRequest,
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

# The sketch depends only on the confirmed plan, so it is written while the user is still wiring.
PREGENERATE = os.getenv("LEARNLENS_PREGENERATE", "1") not in ("0", "false", "")
_background = ThreadPoolExecutor(max_workers=2)
pregenerated: Dict[str, tuple] = {}  # session id -> (plan key, generator, future)


def _plan_key(s: GuideStatus) -> str:
    return json.dumps([s.project.model_dump(mode="json"), [step.model_dump(mode="json") for step in s.wiring_steps]],
                      sort_keys=True)


BACKGROUND_NOTICE = "I am writing the code in the background while you wire."


def _pregenerate(s: GuideStatus) -> None:
    """Start writing, checking and compiling the sketch in the background, and say so. Nothing is uploaded."""
    s.background_notice = ""
    if not PREGENERATE:
        return
    s.background_notice = BACKGROUND_NOTICE
    ctx = codegen.build_context(s.project, s.wiring_steps, previous_code=running_code.get(s.session_id, ""))
    s.generation_progress = []
    future: Future = _background.submit(generator.generate, s.session_id, ctx, s.generation_progress.append)
    pregenerated[s.session_id] = (_plan_key(s), generator, future)
part_seen: Dict[str, str] = {}     # per session: the part a photo of a part to add showed (a suggestion)
running_code: Dict[str, str] = {}  # per session: the sketch before the build was developed further
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
            if best is None and not s.detected_components:
                # Goal first: no photo was looked at, so the user says whether the part is there.
                pending.append(PendingConfirmation(
                    id=f"{cid}-present", component_id=cid, kind=ConfirmationKind.presence,
                    question=f"For this you need the {name}. Do you have one on the desk?",
                    reason="No photo was taken."))
            elif best is None:
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
    s.parts_needed = [facts.catalog()[c]["name"] for c in s.project.components]
    s.pending_confirmations = _pending(s)
    if s.pending_confirmations:
        s.state = GuideState.needs_confirmation
        first = s.pending_confirmations[0]
        _say(s, "Please confirm your parts", first.question)
        return
    # A build that is being developed further keeps its confirmed wiring; only the added steps are open.
    try:
        steps = s.wiring_steps if s.project.requests else planner.plan_wiring(s.project)
    except ValueError:
        # More signals than the board has free inputs of that kind.
        s.state = GuideState.unsupported
        _say(s, "I can't build that yet", "These parts need more input pins than the board has free. "
                                          "Try it with fewer parts.")
        return
    s.safety_problems = facts.check_safety(steps)
    if s.safety_problems:
        s.state = GuideState.blocked
        _say(s, "Stopped: wiring not safe", "I stopped because the wiring plan failed a safety check.")
        return
    first_open = next(i for i in range(len(steps)) if i not in s.confirmed_steps)
    s.wiring_steps, s.current_step, s.state = steps, first_open, GuideState.wiring
    _say(s, steps[first_open].display_text, steps[first_open].spoken_text)
    _pregenerate(s)


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
                    instruction=req.instruction.strip(), display_text="Checking what you need",
                    spoken_text="Let me check which parts you need.")
    _merge_inventory(s, req.confirmed_inventory)
    sessions[s.session_id] = s
    return s


@router.post("/sessions/{session_id}/analyze", response_model=GuideStatus)
async def analyze(session_id: str, image: Optional[UploadFile] = File(None),
                  confirmed_inventory: Optional[str] = Form(None), purpose: Optional[str] = Form(None)):
    s = _load(session_id, GuideState.awaiting_analysis, GuideState.needs_confirmation,
              GuideState.wiring, GuideState.wiring_complete, GuideState.code_generated)
    data, media_type = await _read_image(image)
    if data is None and demo.enabled():
        data, media_type = demo.saved_photo()  # demo mode can start from the saved desk photo

    if purpose == "part" and s.state == GuideState.code_generated:
        # A photo of a part to add to the finished build. It says which part "this sensor" is; the
        # user still confirms the part and its pin labels before anything is wired.
        if data is None:
            raise HTTPException(422, "send an image of the part")
        s.detected_components, s.analysis_source = analyzer.identify(data, media_type, s.project)
        seen = [d for d in s.detected_components if d.component_id in facts.catalog()
                and d.component_id not in s.project.components and d.confidence >= 0.5]
        part_seen.pop(s.session_id, None)
        if seen:
            part_seen[s.session_id] = max(seen, key=lambda d: d.confidence).component_id
        return s

    if s.state in (GuideState.wiring, GuideState.wiring_complete, GuideState.code_generated):
        if data is None:
            raise HTTPException(422, "send an image to review the wiring")
        findings, _ = analyzer.review(data, media_type, s.wiring_steps)
        s.review = WiringReview(observations=findings.observations, possible_issues=findings.possible_issues,
                                electrically_verified=False, disclaimer=REVIEW_DISCLAIMER)
        return s

    # Goal first: a photo is optional. Without one, every part is confirmed by the user.
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
        if not (s.project and s.project.requests):
            s.project = to_spec(analysis.project)  # a build being developed further keeps its project
        elif s.analysis_source == "demo":
            # Demo mode: the saved answer for a photo of the added part.
            s.detected_components = [DetectedComponent.model_validate(demo.SAVED_PART_DETECTION[c])
                                     for c in s.project.components if c in demo.SAVED_PART_DETECTION]
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

    s.current_step, s.state, s.background_notice = None, GuideState.wiring_complete, ""
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
                                req.sample_window_ms, previous_code=running_code.get(s.session_id, ""))
    # Use the sketch that was written during wiring, if it is for exactly this plan and these parameters.
    result = None
    early = pregenerated.pop(s.session_id, None)
    defaults = req.sensor_active_level is None and req.debounce_ms is None and req.sample_window_ms is None
    if early is not None and defaults and early[0] == _plan_key(s) and early[1] is generator:
        try:
            result = early[2].result(timeout=600)  # still running: wait for it; the stages keep showing
        except Exception:
            result = None  # the early attempt failed: generate now
    if result is None:
        try:
            s.generation_progress = []
            # Runs in a worker thread; GET /status can be polled meanwhile to show the current stage.
            result = generator.generate(s.session_id, ctx, s.generation_progress.append)
        except RuntimeError as e:
            raise HTTPException(502, str(e))
    generated, source, attempts = result

    if previous is not None:
        previous.status = BuildStatus.rejected  # replaced by the regenerated sketch
    provenance = Provenance.llm if source == "llm" else Provenance.rules
    sketch = planner.make_sketch(generated.source_code, provenance, filename=f"{codegen.SKETCH_NAME}.ino")
    build = create_awaiting_approval(s.instruction, s.project.components, s.wiring_steps, sketch, provenance)
    build.expected_serial_events = [e.event for e in generated.expected_serial_events]
    s.generation_progress.append("Code ready")
    s.build_id, s.state = build.build_id, GuideState.code_generated
    _say(s, "Code ready. Review it on your phone", "The code is ready. Review and approve it on your phone.")
    return GenerationResult(
        session_id=s.session_id, build_id=build.build_id, source=source, sketch=sketch,
        explanation=generated.explanation, display_summary=codegen.display_summary(generated),
        assumptions=generated.assumptions,
        expected_serial_events=generated.expected_serial_events,
        parameters=({"inputs": [f"{i.name} on {i.header}" for i in ctx.inputs], "fqbn": ctx.fqbn}
                    if ctx.custom_behavior else
                    {"SAMPLE_WINDOW_MS": ctx.sample_window_ms, "sensor_pin": ctx.sensor_pin, "output": ctx.output,
                     "fqbn": ctx.fqbn} if ctx.signal_kind == "analog" else
                    {"SENSOR_ACTIVE_LEVEL": ctx.sensor_active_level, "DEBOUNCE_MS": ctx.debounce_ms,
                     "sensor_pin": ctx.sensor_pin, "led": ctx.led_symbol, "led_on_level": ctx.led_on_level,
                     "fqbn": ctx.fqbn}),
        attempts=attempts, repairs_used=sum(a.kind == "repair" for a in attempts),
        compile=codegen.final_compile(attempts, ctx.fqbn), project_dir=attempts[-1].project_dir)


@router.post("/sessions/{session_id}/extend", response_model=GuideStatus)
def extend(session_id: str, req: ExtendRequest):
    """Develop a finished build further. A new part goes through confirmation and its own wiring
    steps; a behaviour change goes straight to code generation. Either way the new sketch needs
    a new approval before anything is uploaded."""
    s = _load(session_id, GuideState.code_generated)
    build = builds.get(s.build_id) if s.build_id else None
    if build is not None and build.status in (BuildStatus.approved, BuildStatus.deploying):
        raise HTTPException(409, "an upload is in progress")

    proposal, _ = analyzer.extend(req.instruction.strip(), s.project, part_seen.get(s.session_id))
    cat = facts.catalog()

    def cannot(reason: str) -> GuideStatus:
        """The request cannot be built. Nothing changes; the user is told why and what would work."""
        ideas = [" ".join(i.split())[:80] for i in proposal.ideas if i.strip()]
        if not ideas and s.project.game:
            # What always works for a game: the other game, and the restart by hand.
            ideas = [f"Play {games.GAMES[g]['title']} instead" for g in games.offered() if g != s.project.game]
            if games.RESET_SENSOR in addable_sensors(s.project):
                ideas.append("Add the infrared sensor to restart the game")
        elif not ideas and s.project.input_component == "ky-037" and "ky-032" in addable_sensors(s.project):
            ideas = [v["idea"] for v in demo.EXTENSION_VARIANTS.values()]  # these always work
        s.extension_problem, s.suggestions, s.clarification = reason, ideas[:3], ""
        return s

    if proposal.kind == "unsupported" or proposal.kind not in ("add_part", "change_behavior"):
        return cannot(proposal.reason or "I can't do that with the parts I know.")
    steps = list(s.wiring_steps)
    if proposal.kind == "add_part":
        cid = proposal.component_id
        if cid not in cat:
            return cannot("I don't know a part for that yet.")
        if not cat[cid].get("add_on"):
            return cannot(cat[cid].get("not_buildable") or f"I can't build with the {cat[cid]['name']} yet.")
        if cid in s.project.components:
            return cannot(f"The {cat[cid]['name']} is already part of this build.")
        if proposal.needs_purpose or not proposal.behavior.strip():
            # The part is clear, its job is not: ask instead of deciding for the user. Nothing changes yet.
            s.clarification = f"What should the {cat[cid]['name']} do in your build?"
            s.suggestions = [" ".join(i.split())[:80] for i in proposal.ideas if i.strip()][:3]
            s.extension_problem = ""
            return s
        try:
            steps += planner.plan_extension(steps, cid)
        except ValueError:
            return cannot(f"There is no free input pin left on the board for the {cat[cid]['name']}.")

    project = s.project.model_copy(deep=True)
    project.behavior = Behavior.custom
    project.custom_behavior = proposal.behavior or req.instruction.strip()
    project.requests.append(req.instruction.strip())
    if proposal.kind == "add_part":
        project.components.append(proposal.component_id)
    if project.game:
        # A game keeps its prevalidated sketch only for the changes that sketch covers: the other
        # game, and a restart by the infrared sensor. Anything else is free-form and needs the AI.
        other = games.game_for(req.instruction, project.game)
        asked = req.instruction  # the user's own words: the model's text describes the whole game
        if proposal.kind == "change_behavior" and other and other != project.game:
            project.game, project.title, project.summary = other, games.GAMES[other]["title"], games.GAMES[other]["summary"]
            project.custom_behavior = games.behavior(other, reset=games.RESET_SENSOR in project.components)
        elif proposal.kind == "add_part" and proposal.component_id == games.RESET_SENSOR and games.wants_reset(asked):
            project.custom_behavior = games.behavior(project.game, reset=True)
        else:
            project.game = ""
    if generator.client is None:
        # Without the AI service only prevalidated combinations can be built.
        try:
            codegen.template_sketch(codegen.build_context(project, steps))
        except RuntimeError:
            return cannot("Changing what the build does needs the AI service, which is not available right now.")

    if build is not None and build.sketch is not None:
        running_code[s.session_id] = build.sketch.code
    part_seen.pop(s.session_id, None)
    s.project, s.wiring_steps = project, steps
    s.build_id, s.review, s.generation_progress, s.clarification, s.suggestions = None, None, [], "", []
    s.extension_problem = ""
    if proposal.kind == "add_part":
        _advance(s)  # confirmations for the new part, then only the new wiring steps
    else:
        s.current_step, s.state = None, GuideState.wiring_complete
        _say(s, "Updating the code", "No new wiring needed. I am updating the code now.")
    return s


def _answer_context(s: GuideStatus) -> str:
    lines = [f"BUILD: {s.project.title}. {s.project.custom_behavior or s.project.summary}", "WIRING PLAN:"]
    for step in s.wiring_steps:
        mark = "done" if step.index in s.confirmed_steps else "CURRENT STEP" if step.index == s.current_step else "later"
        lines.append(f"- [{mark}] {step.spoken_text or step.display_text}")
    lines.append("VERIFIED FACTS (source quality in brackets):")
    lines += [f"- {f.statement} [{f.confidence.value}]" for f in s.hardware_facts]
    return "\n".join(lines)


def _short(text: str, limit: int = 220) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rsplit(" ", 1)[0] + "…"


@router.post("/sessions/{session_id}/ask", response_model=StepAnswer)
def ask(session_id: str, req: AskRequest):
    """Answer a question during the build. Changes nothing in the session."""
    s = _load(session_id)
    if s.project is None:
        raise HTTPException(409, "there is no build to ask about yet")
    answer = analyzer.answer(req.question.strip(), _answer_context(s))
    if answer:
        return StepAnswer(question=req.question, answer=_short(answer), source="llm", step_index=s.current_step)
    # No AI: say what is verified about the current step instead of guessing.
    step = s.wiring_steps[s.current_step] if s.current_step is not None else None
    known = [f.statement for f in s.hardware_facts if step and f.id in step.fact_ids]
    text = ("I can't answer free questions right now. What I know for this step: " + " ".join(known)) if known \
        else "I can't answer free questions right now. Follow the step as shown; it was checked against the board's limits."
    return StepAnswer(question=req.question, answer=_short(text, 320), source="facts", step_index=s.current_step)


@router.get("/sessions/{session_id}/status", response_model=GuideStatus)
def status(session_id: str):
    return _load(session_id)
