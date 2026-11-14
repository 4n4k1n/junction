"""Shared data models: lesson content, session state, API payloads and the tutor contract."""
from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


# ---------- Lesson content (read-only, from lessons/*.json) ----------

class Verification(str, Enum):
    question = "question"                       # a correct answer to check_question
    vision_and_question = "vision_and_question"  # verified photo, then a correct answer
    visual_and_manual = "visual_and_manual"      # verified photo OR manual confirm, then a correct answer
    manual = "manual"                            # only POST /confirm


class Step(BaseModel):
    id: str
    title: str
    goal: str
    intro_display: str
    intro_spoken: str
    visual_success_criteria: str = ""
    hint_levels: List[str]
    check_question: str
    expected_concepts: List[List[str]] = []
    min_concepts: int = 1
    verification: Verification


class Lesson(BaseModel):
    lesson_id: str
    title: str
    safety: List[str] = []
    materials: List[str] = []
    steps: List[Step]


# ---------- Session state (owned by the backend state machine) ----------

class HistoryItem(BaseModel):
    role: str  # "learner" | "tutor"
    text: str
    step_id: str


class SessionState(BaseModel):
    session_id: str
    lesson_id: str
    step_index: int = 0
    hint_level: int = 0             # hints already given in the current step
    visual_ok: bool = False         # photo of the current step was verified
    question_pending: bool = False  # check_question was asked and awaits an answer
    attempts: int = 0               # interactions in the current step
    completed_steps: List[str] = []
    complete: bool = False
    history: List[HistoryItem] = []


# ---------- Tutor contract (what the LLM must return) ----------

class TutorAction(str, Enum):
    give_hint = "give_hint"
    ask_question = "ask_question"
    request_image = "request_image"
    confirm_progress = "confirm_progress"
    explain = "explain"


class VerificationStatus(str, Enum):
    verified = "verified"
    uncertain = "uncertain"
    failed = "failed"
    not_applicable = "not_applicable"


class TutorDecision(BaseModel):
    action: TutorAction
    visual_observation: str = ""
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    display_text: str
    spoken_text: str
    verification_status: VerificationStatus = VerificationStatus.not_applicable
    answer_correct: Optional[bool] = None
    next_step_allowed: bool = False


# JSON schema handed to Claude via output_config.format (strict: additionalProperties false).
TUTOR_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": [a.value for a in TutorAction]},
        "visual_observation": {"type": "string"},
        "confidence": {"type": "number"},
        "display_text": {"type": "string"},
        "spoken_text": {"type": "string"},
        "verification_status": {"type": "string", "enum": [v.value for v in VerificationStatus]},
        "answer_correct": {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        "next_step_allowed": {"type": "boolean"},
    },
    "required": [
        "action", "visual_observation", "confidence", "display_text", "spoken_text",
        "verification_status", "answer_correct", "next_step_allowed",
    ],
    "additionalProperties": False,
}


# ---------- API payloads ----------

class InteractionType(str, Enum):
    photo = "photo"        # learner took a photo (optionally with a message)
    question = "question"  # learner asks something
    answer = "answer"      # learner answers the pending check question
    hint = "hint"          # learner explicitly asks for a hint


class CreateSessionRequest(BaseModel):
    lesson_id: str = "led-basics"


class ConfirmRequest(BaseModel):
    note: str = ""


class StepView(BaseModel):
    step_id: str
    step_index: int
    total_steps: int
    title: str
    hint_level: int
    max_hints: int


class TutorResponse(BaseModel):
    session_id: str
    action: TutorAction
    display_text: str
    spoken_text: str
    step: StepView
    verification_status: VerificationStatus
    next_step_allowed: bool
    advanced: bool = False
    lesson_complete: bool = False
    visual_observation: str = ""
    confidence: float = 0.0
    source: str = "engine"  # "llm" | "mock" | "fallback" | "engine"
    latency_ms: int = 0
