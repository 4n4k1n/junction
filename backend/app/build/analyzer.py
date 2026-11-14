"""Looks at the photo and the instruction and proposes: which known parts are visible, and what
the project is. That is all the model decides. Pins, voltages and wiring come from the hardware
database (planner.py), and a photo review never counts as an electrical check.

ClaudeAnalyzer  - Anthropic Messages API with image input and a strict JSON schema
MockAnalyzer    - offline: no vision, keyword-based project
Analyzer        - facade: Claude first; on any failure it reports that it saw nothing, so the
                  session asks the user to confirm the parts instead of guessing
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field, ValidationError

from . import demo, facts
from .llm import MODEL, ask_json, make_client  # noqa: F401  (MODEL re-exported for scripts)
from .models import Behavior, DetectedComponent, ProjectSpec, WiringStep
from .planner import BOARD

log = logging.getLogger("learnlens.analyzer")

EFFORT = os.getenv("LEARNLENS_ANALYZER_EFFORT", "medium")
UNKNOWN = "unknown"


class ProjectProposal(BaseModel):
    title: str
    summary: str
    behavior: Behavior
    input_component: str  # a sensor id from the hardware database, or "none"
    unsupported_reason: str = ""


class Analysis(BaseModel):
    components: List[DetectedComponent]
    project: ProjectProposal


class ReviewFindings(BaseModel):
    observations: List[str] = Field(default_factory=list)
    possible_issues: List[str] = Field(default_factory=list)


def _sensor_ids() -> List[str]:
    return [cid for cid, c in facts.catalog().items() if c["role"] == "sensor"]


def analysis_schema() -> dict:
    """Strict schema: component ids are an enum of the hardware database, so nothing can be invented."""
    return {
        "type": "object",
        "properties": {
            "components": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "component_id": {"type": "string", "enum": sorted(facts.catalog()) + [UNKNOWN]},
                        "label": {"type": "string"},
                        "confidence": {"type": "number"},
                        "evidence": {"type": "string"},
                    },
                    "required": ["component_id", "label", "confidence", "evidence"],
                    "additionalProperties": False,
                },
            },
            "project": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "behavior": {"type": "string", "enum": [b.value for b in Behavior]},
                    "input_component": {"type": "string", "enum": _sensor_ids() + ["none"]},
                    "unsupported_reason": {"type": "string"},
                },
                "required": ["title", "summary", "behavior", "input_component", "unsupported_reason"],
                "additionalProperties": False,
            },
        },
        "required": ["components", "project"],
        "additionalProperties": False,
    }


REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {"type": "array", "items": {"type": "string"}},
        "possible_issues": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["observations", "possible_issues"],
    "additionalProperties": False,
}


def _catalog_block() -> str:
    # Stable across requests -> prompt-cache prefix. Appearance only: no pins, no voltages.
    lines = [f"- {cid} ({c['name']}, {c['role']}): {c['identification']}" for cid, c in sorted(facts.catalog().items())]
    return "KNOWN PARTS (the only component_id values you may use, besides \"unknown\"):\n" + "\n".join(lines)


ANALYZE_SYSTEM = """You are the vision step of LearnLens, a hardware-building assistant on smart glasses.
A beginner shows you a photo of the parts on their desk and says what they want to build.
The outputs are the board's built-in LED or its LED matrix. Your answer feeds a rule-based planner, and a
person will then confirm every part by hand, so being honest about doubt matters more than being helpful.

components: one entry for each electronic part you can see.
- component_id: use a known part's id only when the photo shows the features listed for it.
  Anything else, including a board or sensor that merely resembles a known part, is "unknown".
- label: what you see, in plain words ("red sensor board with a microphone capsule").
- confidence: 0 to 1, how sure you are of the component_id. Use 0.9 or more only when you can read
  a marking or see every listed feature. Blurry, cropped or partly hidden parts stay below 0.6.
- evidence: the visible features or printed text behind your call, and what you could not see.
If there is no photo, return an empty components list.

project: what the user wants, restricted to what the known parts can do.
- behavior: toggle_on_event (each event flips the LED), on_while_active (LED follows the sensor),
  level_meter (the louder the sound, the more LEDs of the board's LED matrix light up),
  or unsupported when the request needs parts or outputs that are not known parts.
- input_component: the known sensor that detects the trigger, or "none".
- unsupported_reason: one sentence for the user when unsupported, otherwise empty.
- title: at most 5 words. summary: one sentence.

Never state pin names, voltages, wiring or electrical ratings anywhere in your answer. Those come
from a verified hardware database, not from you."""

REVIEW_SYSTEM = """You are reviewing a photo of a beginner's wiring for LearnLens. You get the list of
connections they were asked to make. Describe what the photo shows and point out anything that
looks different from the list.

A photo cannot prove an electrical connection: a wire can look seated and not make contact, and
labels are often unreadable. So never say a connection is correct, verified, safe or working.
Say "appears to", and say what you cannot see.

observations: up to 4 short factual statements about what is visible.
possible_issues: up to 4 short things for the user to re-check by hand. Empty if nothing stands out."""


def keyword_project(instruction: str) -> ProjectProposal:
    """Offline stand-in for the model's project proposal."""
    text = instruction.lower()
    meter_words = ("level", "meter", "volume", "loud", "matrix", "lautst", "pegel", "lauter", "leds", "more led",
                   "many led", "viele led", "mehr led", "analog", "aout", " ao ", "higher", "stronger", "bar")
    if any(w in text for w in meter_words):
        return ProjectProposal(title="Sound level meter",
                               summary="The louder it gets, the more of the LED matrix lights up.",
                               behavior=Behavior.level_meter, input_component="ky-037")
    if any(w in text for w in ("clap", "sound", "noise", "klatsch")):
        return ProjectProposal(title="Clap-activated light", summary="A clap toggles the board's built-in LED.",
                               behavior=Behavior.toggle_on_event, input_component="ky-037")
    return ProjectProposal(title="Unsupported project", summary=instruction[:80], behavior=Behavior.unsupported,
                           input_component="none",
                           unsupported_reason="Without the AI service I can only set up the clap-activated light "
                                              "and the sound level meter.")


def to_spec(p: ProjectProposal) -> ProjectSpec:
    """Check the proposal against the hardware database and turn it into a project spec."""
    sensors = _sensor_ids()
    reason = p.unsupported_reason
    if p.behavior == Behavior.unsupported:
        reason = reason or "This project needs parts I do not support yet."
    elif p.input_component not in sensors:
        reason = "I need a supported sensor for that. Right now I know: " + ", ".join(sensors) + "."
    elif ("led_matrix" if p.behavior == Behavior.level_meter else "builtin_led") not in facts.catalog()[BOARD]:
        reason = "The board output this needs is not verified in the hardware database."
    else:
        reason = ""
    sensor = p.input_component if p.input_component in sensors else None
    return ProjectSpec(title=p.title, summary=p.summary, behavior=p.behavior if not reason else Behavior.unsupported,
                       board=BOARD, input_component=sensor, components=[BOARD] + ([sensor] if sensor else []),
                       output="led_matrix" if p.behavior == Behavior.level_meter else "builtin_led",
                       supported=not reason, unsupported_reason=reason)


def _steps_block(steps: List[WiringStep]) -> str:
    lines = [f"- {s.connection.from_pin.component} pin {s.connection.from_pin.pin} -> "
             f"{s.connection.to_pin.component} pin {s.connection.to_pin.pin}" for s in steps if s.connection]
    return "CONNECTIONS THE USER WAS ASKED TO MAKE:\n" + "\n".join(lines)


class MockAnalyzer:
    """No vision. Detects nothing unless LEARNLENS_MOCK_DETECTION=confident (rehearsals without an API key)."""

    def analyze(self, instruction: str, image: Optional[bytes], media_type: str) -> Analysis:
        project = keyword_project(instruction)
        components: List[DetectedComponent] = []
        if image and os.getenv("LEARNLENS_MOCK_DETECTION", "none") == "confident":
            components = [DetectedComponent(component_id=cid, label=facts.catalog()[cid]["name"], confidence=0.9,
                                            evidence="(mock) not actually looked at")
                          for cid in to_spec(project).components]
        return Analysis(components=components, project=project)

    def review(self, image: bytes, media_type: str, steps: List[WiringStep]) -> ReviewFindings:
        return ReviewFindings(observations=["(mock) the photo was not analyzed"],
                              possible_issues=["Re-check each wire against the steps by hand."])


class ClaudeAnalyzer:
    def __init__(self, client=None) -> None:
        self.client = client or make_client()

    def _ask(self, system: List[dict], content: List[dict], schema: dict) -> dict:
        return ask_json(self.client, system, content, schema, EFFORT)

    @staticmethod
    def _image(image: bytes, media_type: str) -> dict:
        return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                            "data": base64.standard_b64encode(image).decode("ascii")}}

    def analyze(self, instruction: str, image: Optional[bytes], media_type: str) -> Analysis:
        content = [self._image(image, media_type)] if image else []
        content.append({"type": "text", "text": f"USER WANTS TO BUILD: {instruction}\n"
                                                + ("" if image else "(no photo was provided)")})
        system = [{"type": "text", "text": ANALYZE_SYSTEM},
                  {"type": "text", "text": _catalog_block(), "cache_control": {"type": "ephemeral"}}]
        analysis = Analysis.model_validate(self._ask(system, content, analysis_schema()))
        if not image:
            analysis.components = []  # nothing to see, whatever the model says
        for c in analysis.components:
            if c.component_id not in facts.catalog():
                c.component_id = UNKNOWN  # an id outside the database is never passed on
        return analysis

    def review(self, image: bytes, media_type: str, steps: List[WiringStep]) -> ReviewFindings:
        content = [self._image(image, media_type), {"type": "text", "text": _steps_block(steps)}]
        return ReviewFindings.model_validate(
            self._ask([{"type": "text", "text": REVIEW_SYSTEM}], content, REVIEW_SCHEMA))


class Analyzer:
    """Facade. Returns (result, source) with source in llm | mock | fallback."""

    def __init__(self, mode: Optional[str] = None, claude: Optional[ClaudeAnalyzer] = None) -> None:
        default = "demo" if demo.enabled() else os.getenv("LEARNLENS_ANALYZER") or os.getenv("LEARNLENS_TUTOR", "auto")
        self.mode = mode or default  # auto | claude | mock | demo
        self.mock = MockAnalyzer()
        self.claude = claude
        if self.claude is None and self.mode in ("auto", "claude"):
            try:
                self.claude = ClaudeAnalyzer()
            except Exception as e:  # SDK missing or no credentials
                if self.mode == "claude":
                    raise
                log.warning("Claude unavailable, using mock analyzer: %s", e)

    def analyze(self, instruction: str, image: Optional[bytes] = None,
                media_type: str = "image/jpeg") -> Tuple[Analysis, str]:
        if self.mode == "demo":
            # Saved detection; the project follows the instruction by keyword, so it stays deterministic.
            saved = Analysis.model_validate(demo.SAVED_ANALYSIS)
            proposal = keyword_project(instruction)
            if proposal.behavior != Behavior.unsupported:
                saved.project = proposal
            return saved, "demo"
        if self.claude is None:
            return self.mock.analyze(instruction, image, media_type), "mock"
        try:
            return self.claude.analyze(instruction, image, media_type), "llm"
        except (ValidationError, json.JSONDecodeError, StopIteration) as e:
            log.error("invalid analysis JSON: %s", e)
        except Exception as e:
            log.exception("analysis call failed: %s", e)
        # Detect nothing rather than guess: the session then asks the user to confirm each part.
        return Analysis(components=[], project=keyword_project(instruction)), "fallback"

    def review(self, image: bytes, media_type: str, steps: List[WiringStep]) -> Tuple[ReviewFindings, str]:
        if self.claude is None:
            return self.mock.review(image, media_type, steps), "mock"
        try:
            return self.claude.review(image, media_type, steps), "llm"
        except Exception as e:
            log.exception("review call failed: %s", e)
        return ReviewFindings(observations=[], possible_issues=["The photo could not be analyzed. "
                                                                 "Re-check each wire by hand."]), "fallback"
