"""Deterministic demo mode (LEARNLENS_DEMO_MODE=1): no AI calls.

The analysis is a fixed, saved answer and the sketch is the prevalidated rule-based one, so the
demo behaves the same every time and works without network. Everything else is the real code
path: part confirmation, safety checks, static checks, compile, approval, and the deploy agent
(which still flashes the real board unless it runs with --mock).

Every response produced this way says so: analysis_source and GenerationResult.source are "demo",
and GET /health reports demo_mode.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Tuple

SAVED_PHOTO = Path(os.getenv("LEARNLENS_DEMO_PHOTO", Path(__file__).resolve().parents[3] / "demo" / "desk.jpg"))

# A saved answer in the shape the vision model returns: sure about the board, unsure about the
# sensor, so the demo shows the confirmation step.
SAVED_ANALYSIS = {
    "components": [
        {"component_id": "uno-q", "label": "Arduino board with an LED matrix", "confidence": 0.94,
         "evidence": "'UNO Q' is printed next to the LED matrix; USB-C connector visible."},
        {"component_id": "ky-037", "label": "red sensor board with a microphone capsule", "confidence": 0.62,
         "evidence": "Microphone capsule, blue trim pot and 4 pins visible. Pin labels are not readable, "
                     "and the capsule size could also match a KY-038."},
        {"component_id": "unknown", "label": "bundle of jumper wires", "confidence": 0.9, "evidence": "Loose wires."},
    ],
    "project": {"title": "Sound level meter", "summary": "The louder it gets, the more of the LED matrix lights up.",
                "behavior": "level_meter", "input_component": "ky-037", "unsupported_reason": ""},
}


def enabled() -> bool:
    return os.getenv("LEARNLENS_DEMO_MODE", "") not in ("", "0", "false")


def saved_photo() -> Tuple[Optional[bytes], str]:
    """The photo to fall back on when a demo request carries none (demo/desk.jpg, if you saved one)."""
    if SAVED_PHOTO.is_file():
        return SAVED_PHOTO.read_bytes(), "image/jpeg"
    return None, ""
