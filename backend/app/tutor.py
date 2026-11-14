"""Tutor decision makers.

ClaudeTutor  – multimodal LLM (image + context -> structured TutorDecision)
MockTutor    – deterministic offline answers for tests and as a demo fallback

`decide()` never raises: any LLM failure falls back to MockTutor so the demo keeps running.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Optional, Tuple

from pydantic import ValidationError

from .engine import current_step, grade_answer
from .models import (
    InteractionType, Lesson, SessionState, TUTOR_JSON_SCHEMA, TutorAction, TutorDecision,
    VerificationStatus,
)

log = logging.getLogger("learnlens.tutor")

MODEL = os.getenv("LEARNLENS_MODEL", "claude-opus-5-5")
EFFORT = os.getenv("LEARNLENS_EFFORT", "low")  # low keeps time-to-hint short for the live demo
TIMEOUT_S = float(os.getenv("LEARNLENS_LLM_TIMEOUT", "20"))

SYSTEM_PROMPT = """You are LearnLens, a Socratic AI tutor running on smart glasses.
Your mission is to help learners understand concepts through hands-on practice.

Rules:
1. Never immediately reveal the full solution.
2. Give one small hint at a time. Use the lesson's hint for the current hint level when it fits.
3. Ask questions before providing explanations.
4. Adapt to the learner's current step only; do not jump ahead.
5. display_text must be under 12 words. spoken_text: at most 2 short sentences.
6. Never claim to see something that is unclear in the image.
7. If visual evidence is insufficient, use action "request_image" and verification_status "uncertain".
8. You only judge. The backend decides whether the learner advances; next_step_allowed is only your recommendation.
9. Follow the lesson's safety constraints. If something looks unsafe, say so first.
10. Respond in the learner's language.

Field guide:
- verification_status: "verified" only if the photo clearly meets the step's visual success criteria;
  "failed" if it clearly does not; "uncertain" if you cannot tell; "not_applicable" without a photo.
- confidence: your own estimate 0..1 for the visual judgement (not calibrated).
- answer_correct: true/false when the learner answered the step's check question, otherwise null.
  Judge understanding, not exact wording.
- visual_observation: a short factual description of what you actually see (empty without a photo).
"""


def _lesson_block(lesson: Lesson) -> str:
    # Stable across the whole session -> good prompt-cache prefix.
    return "LESSON (JSON):\n" + lesson.model_dump_json(indent=1)


def _context_block(lesson: Lesson, s: SessionState, interaction: InteractionType, msg: str) -> str:
    step = current_step(lesson, s)
    history = "\n".join(f"{h.role}: {h.text}" for h in s.history[-8:]) or "(none)"
    return (
        f"CURRENT STEP: {step.id} ({s.step_index + 1}/{len(lesson.steps)}) – {step.title}\n"
        f"Goal: {step.goal}\n"
        f"Visual success criteria: {step.visual_success_criteria or '(none – no photo needed)'}\n"
        f"Verification type: {step.verification.value}\n"
        f"Hints already given: {s.hint_level}/{len(step.hint_levels)}"
        f" (next hint: {step.hint_levels[min(s.hint_level, len(step.hint_levels) - 1)]!r})\n"
        f"Photo already verified for this step: {s.visual_ok}\n"
        f"Check question pending: {s.question_pending} – {step.check_question!r}\n"
        f"Recent conversation:\n{history}\n\n"
        f"INTERACTION TYPE: {interaction.value}\n"
        f"LEARNER SAYS: {msg or '(nothing)'}"
    )


class MockTutor:
    """Offline tutor. Set LEARNLENS_MOCK_VISION=verified to make photos pass during a rehearsal."""

    def decide(self, lesson: Lesson, s: SessionState, interaction: InteractionType,
               msg: str, image: Optional[bytes], media_type: str) -> TutorDecision:
        step = current_step(lesson, s)
        hint = step.hint_levels[min(s.hint_level, len(step.hint_levels) - 1)]

        if interaction == InteractionType.photo:
            if os.getenv("LEARNLENS_MOCK_VISION", "uncertain") == "verified" and step.visual_success_criteria:
                return TutorDecision(action=TutorAction.confirm_progress, confidence=0.9,
                                     visual_observation="(mock) setup looks as expected",
                                     display_text="Looks good!", spoken_text="That looks right.",
                                     verification_status=VerificationStatus.verified)
            return TutorDecision(action=TutorAction.request_image, confidence=0.3,
                                 display_text="Can't verify. Test it manually?",
                                 spoken_text="I can't verify the wiring from this photo. Try it, then confirm.",
                                 verification_status=VerificationStatus.uncertain)

        if interaction == InteractionType.answer or s.question_pending:
            ok = grade_answer(step, msg)
            return TutorDecision(
                action=TutorAction.confirm_progress if ok else TutorAction.give_hint,
                display_text="Correct!" if ok else hint,
                spoken_text="Exactly." if ok else "Not quite. " + hint,
                answer_correct=ok, next_step_allowed=ok,
            )

        if interaction == InteractionType.question:
            return TutorDecision(action=TutorAction.ask_question, display_text=hint,
                                 spoken_text="Good question. " + hint)

        return TutorDecision(action=TutorAction.give_hint, display_text=hint, spoken_text=hint)


class ClaudeTutor:
    def __init__(self) -> None:
        import anthropic  # imported lazily so tests/mocks run without the SDK configured

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(timeout=TIMEOUT_S, max_retries=1)

    def decide(self, lesson: Lesson, s: SessionState, interaction: InteractionType,
               msg: str, image: Optional[bytes], media_type: str) -> TutorDecision:
        content = []
        if image:
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": media_type,
                           "data": base64.standard_b64encode(image).decode("ascii")},
            })
        content.append({"type": "text", "text": _context_block(lesson, s, interaction, msg)})

        response = self.client.beta.messages.create(
            model=MODEL,
            max_tokens=4000,
            system=[
                {"type": "text", "text": SYSTEM_PROMPT},
                {"type": "text", "text": _lesson_block(lesson), "cache_control": {"type": "ephemeral"}},
            ],
            messages=[{"role": "user", "content": content}],
            output_config={"effort": EFFORT, "format": {"type": "json_schema", "schema": TUTOR_JSON_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # on a safety refusal the API retries on a fallback model
        )
        if response.stop_reason == "refusal":
            raise RuntimeError(f"model refused: {getattr(response.stop_details, 'category', None)}")
        if response.stop_reason == "max_tokens":
            raise RuntimeError("model hit max_tokens")
        text = next(b.text for b in response.content if b.type == "text")
        return TutorDecision.model_validate(json.loads(text))


class Tutor:
    """Facade: try Claude, fall back to the mock. Returns (decision, source)."""

    def __init__(self, mode: Optional[str] = None) -> None:
        self.mode = mode or os.getenv("LEARNLENS_TUTOR", "auto")  # auto | claude | mock
        self.mock = MockTutor()
        self.claude: Optional[ClaudeTutor] = None
        if self.mode in ("auto", "claude"):
            try:
                self.claude = ClaudeTutor()
            except Exception as e:  # SDK missing or no credentials
                if self.mode == "claude":
                    raise
                log.warning("Claude unavailable, using mock tutor: %s", e)

    def decide(self, lesson, s, interaction, msg, image=None, media_type="image/jpeg") -> Tuple[TutorDecision, str]:
        if self.claude is not None:
            try:
                return self.claude.decide(lesson, s, interaction, msg, image, media_type), "llm"
            except (ValidationError, json.JSONDecodeError, StopIteration) as e:
                log.error("invalid tutor JSON, falling back: %s", e)
            except self.claude._anthropic.APIConnectionError as e:
                log.error("LLM unreachable, falling back: %s", e)
            except self.claude._anthropic.APIStatusError as e:
                log.error("LLM API error %s, falling back: %s", e.status_code, e.message)
            except Exception as e:
                log.exception("LLM call failed, falling back: %s", e)
            return self.mock.decide(lesson, s, interaction, msg, image, media_type), "fallback"
        return self.mock.decide(lesson, s, interaction, msg, image, media_type), "mock"
