"""Deterministic learning state machine.

The LLM only *proposes* (TutorDecision). This module decides what actually happens:
hint escalation, when the check question is asked, and whether the learner advances.
The model can never move progress on its own.
"""
from __future__ import annotations

import re
from typing import Optional

from .models import (
    HistoryItem, InteractionType, Lesson, SessionState, Step, StepView, TutorAction,
    TutorDecision, TutorResponse, Verification, VerificationStatus,
)

MAX_DISPLAY_WORDS = 12
MIN_VISUAL_CONFIDENCE = 0.7
HISTORY_LIMIT = 12

VISUAL_STEPS = {Verification.vision_and_question, Verification.visual_and_manual}
QUESTION_STEPS = {Verification.question, Verification.vision_and_question, Verification.visual_and_manual}


# ---------- helpers ----------

def clamp_display(text: str, max_words: int = MAX_DISPLAY_WORDS) -> str:
    words = text.strip().split()
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(".,;:") + "…"


def grade_answer(step: Step, answer: str) -> bool:
    """Keyword grading — the offline fallback and a sanity check for LLM grading."""
    text = " " + re.sub(r"[^\w+ ]", " ", answer.lower()) + " "
    hits = sum(1 for group in step.expected_concepts if any(kw.lower() in text for kw in group))
    return hits >= min(step.min_concepts, len(step.expected_concepts) or 1)


def current_step(lesson: Lesson, s: SessionState) -> Step:
    return lesson.steps[min(s.step_index, len(lesson.steps) - 1)]


def step_view(lesson: Lesson, s: SessionState) -> StepView:
    step = current_step(lesson, s)
    return StepView(
        step_id=step.id, step_index=s.step_index, total_steps=len(lesson.steps),
        title=step.title, hint_level=s.hint_level, max_hints=len(step.hint_levels),
    )


def _remember(s: SessionState, role: str, text: str, step_id: str) -> None:
    if text:
        s.history.append(HistoryItem(role=role, text=text, step_id=step_id))
        s.history = s.history[-HISTORY_LIMIT:]


def _next_hint(step: Step, s: SessionState) -> str:
    idx = min(s.hint_level, len(step.hint_levels) - 1)
    s.hint_level = min(s.hint_level + 1, len(step.hint_levels))
    return step.hint_levels[idx]


def _advance(lesson: Lesson, s: SessionState) -> Optional[Step]:
    """Move to the next step. Returns the new step, or None if the lesson is complete."""
    s.completed_steps.append(current_step(lesson, s).id)
    s.hint_level = 0
    s.visual_ok = False
    s.question_pending = False
    s.attempts = 0
    if s.step_index + 1 >= len(lesson.steps):
        s.complete = True
        return None
    s.step_index += 1
    return lesson.steps[s.step_index]


# ---------- public API ----------

def intro(lesson: Lesson, s: SessionState) -> TutorResponse:
    step = current_step(lesson, s)
    if step.verification == Verification.question:
        s.question_pending = True
    return TutorResponse(
        session_id=s.session_id, action=TutorAction.explain,
        display_text=clamp_display(step.intro_display), spoken_text=step.intro_spoken,
        step=step_view(lesson, s), verification_status=VerificationStatus.not_applicable,
        next_step_allowed=False, lesson_complete=s.complete,
    )


def apply_decision(
    lesson: Lesson,
    s: SessionState,
    interaction: InteractionType,
    user_message: str,
    decision: TutorDecision,
    source: str,
) -> TutorResponse:
    step = current_step(lesson, s)
    s.attempts += 1
    _remember(s, "learner", user_message, step.id)

    if s.complete:
        return _respond(lesson, s, TutorAction.explain, "Lesson complete. Well done!",
                        "You finished the lesson. Great work!", decision, source)

    action = decision.action
    display = decision.display_text
    spoken = decision.spoken_text
    status = decision.verification_status

    # 1) Vision guardrails: low confidence never counts as verified.
    if interaction == InteractionType.photo:
        if status == VerificationStatus.verified and decision.confidence < MIN_VISUAL_CONFIDENCE:
            status = VerificationStatus.uncertain
        if step.verification in VISUAL_STEPS and status == VerificationStatus.verified:
            s.visual_ok = True
        elif status == VerificationStatus.uncertain and action == TutorAction.confirm_progress:
            action = TutorAction.request_image
            display = "Not sure. Take a closer photo?"
            spoken = "I can't see that clearly. Please take another photo, a bit closer."

    # 2) Answer grading for the pending check question.
    answer_ok = False
    if interaction == InteractionType.answer or (s.question_pending and interaction == InteractionType.question):
        if decision.answer_correct is not None:
            answer_ok = decision.answer_correct
        else:
            answer_ok = grade_answer(step, user_message)
        if not answer_ok and interaction == InteractionType.answer:
            action = TutorAction.give_hint
            if not display or decision.action != TutorAction.give_hint:
                display = step.hint_levels[min(s.hint_level, len(step.hint_levels) - 1)]
            s.hint_level = min(s.hint_level + 1, len(step.hint_levels))

    # 3) Explicit hint request: always the engine's next hint level (deterministic).
    if interaction == InteractionType.hint:
        action = TutorAction.give_hint
        display = _next_hint(step, s)
        spoken = spoken or display
    elif action == TutorAction.give_hint and interaction != InteractionType.answer:
        s.hint_level = min(s.hint_level + 1, len(step.hint_levels))

    # 4) Gate: is the step done?
    gate_open = _gate_open(step, s, answer_ok)

    # Visual part done but question still open -> ask the check question now.
    if not gate_open and step.verification in VISUAL_STEPS and s.visual_ok and not s.question_pending:
        s.question_pending = True
        action = TutorAction.ask_question
        display = step.check_question
        spoken = decision.spoken_text or ""
        if step.check_question.lower().rstrip("?") not in spoken.lower():
            spoken = (spoken + " " + step.check_question).strip()

    if gate_open:
        nxt = _advance(lesson, s)
        if nxt is None:
            resp = _respond(lesson, s, TutorAction.confirm_progress, "All steps done. Well done!",
                            "Excellent. You built, debugged and explained your LED circuit.",
                            decision, source, status=status, advanced=True)
            return resp
        if nxt.verification == Verification.question:
            s.question_pending = True
        resp = _respond(lesson, s, TutorAction.confirm_progress, "✓ " + nxt.intro_display,
                        "Correct. " + nxt.intro_spoken, decision, source, status=status, advanced=True)
        return resp

    if action == TutorAction.ask_question and display.strip() == step.check_question:
        s.question_pending = True

    return _respond(lesson, s, action, display, spoken, decision, source, status=status)


def manual_confirm(lesson: Lesson, s: SessionState) -> TutorResponse:
    """Learner/demo operator confirms the physical result (e.g. 'the LED lights up')."""
    step = current_step(lesson, s)
    if s.complete:
        return intro(lesson, s)
    if step.verification in (Verification.manual, Verification.visual_and_manual):
        decision = TutorDecision(action=TutorAction.confirm_progress, display_text="", spoken_text="",
                                 verification_status=VerificationStatus.verified, confidence=1.0)
        nxt = _advance(lesson, s)
        if nxt is None:
            return _respond(lesson, s, TutorAction.confirm_progress, "All steps done. Well done!",
                            "You finished the lesson.", decision, "engine", advanced=True)
        if nxt.verification == Verification.question:
            s.question_pending = True
        return _respond(lesson, s, TutorAction.confirm_progress, "✓ " + nxt.intro_display,
                        "Confirmed. " + nxt.intro_spoken, decision, "engine", advanced=True)
    # Steps that need understanding cannot be skipped by a manual confirm.
    decision = TutorDecision(action=TutorAction.ask_question, display_text="", spoken_text="")
    s.question_pending = True
    return _respond(lesson, s, TutorAction.ask_question, step.check_question,
                    "First answer this: " + step.check_question, decision, "engine")


def _gate_open(step: Step, s: SessionState, answer_ok: bool) -> bool:
    v = step.verification
    if v == Verification.manual:
        return False
    if v == Verification.question:
        return answer_ok
    # visual steps: photo verified first, then the check question
    return s.visual_ok and s.question_pending and answer_ok


def _respond(lesson, s, action, display, spoken, decision, source, status=None, advanced=False) -> TutorResponse:
    display = clamp_display(display or "Take a photo or ask me.")
    spoken = spoken or display
    _remember(s, "tutor", spoken, current_step(lesson, s).id)
    return TutorResponse(
        session_id=s.session_id,
        action=action,
        display_text=display,
        spoken_text=spoken,
        step=step_view(lesson, s),
        verification_status=status or decision.verification_status,
        next_step_allowed=advanced,
        advanced=advanced,
        lesson_complete=s.complete,
        visual_observation=decision.visual_observation,
        confidence=decision.confidence,
        source=source,
    )
