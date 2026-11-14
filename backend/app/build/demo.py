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


# What the vision step "sees" in demo mode when a part is added to a finished build.
SAVED_PART_DETECTION = {
    "ky-032": {"component_id": "ky-032", "label": "small board with two LEDs sticking out at the front",
               "confidence": 0.91, "evidence": "Two LEDs side by side, two blue trim pots and a jumper are visible. "
                                               "The pin labels are not readable."},
}

# Ways to develop the level meter further with the infrared sensor that have a prevalidated sketch.
# These work without any AI call, and they are the ideas offered when there is no AI.
EXTENSION_VARIANTS = {
    "gate": {
        "idea": "Show the sound level only while a hand is near",
        "behavior": "Show the sound level on the LED matrix only while the infrared sensor detects something in "
                    "front of it. Otherwise the matrix stays dark.",
        "summary": "The sound level appears on the LED matrix only while the infrared sensor sees something in "
                   "front of it.",
    },
    "freeze": {
        "idea": "Hold a hand near to freeze the level",
        "behavior": "Show the sound level as before. While the infrared sensor detects something in front of it, "
                    "freeze the bar at its current level.",
        "summary": "The bar follows the sound level. Hold a hand in front of the infrared sensor and it freezes.",
    },
    "toggle": {
        "idea": "Wave a hand to switch the meter on and off",
        "behavior": "Each time the infrared sensor newly detects something in front of it, switch the sound level "
                    "display on or off.",
        "summary": "Wave a hand past the infrared sensor to switch the sound level display on or off.",
    },
}


def variant_for(text: str) -> str:
    """Which prevalidated variant a request or behaviour description means. Defaults to "gate"."""
    text = text.lower()
    if any(w in text for w in ("freeze", "hold", "einfrier", "halte", "anhalten")):
        return "freeze"
    if any(w in text for w in ("wave", "toggle", "switch", "on and off", "on or off", "ein und aus", "umschalt", "wink")):
        return "toggle"
    return "gate"


def enabled() -> bool:
    return os.getenv("LEARNLENS_DEMO_MODE", "") not in ("", "0", "false")


def saved_photo() -> Tuple[Optional[bytes], str]:
    """The photo to fall back on when a demo request carries none (demo/desk.jpg, if you saved one)."""
    if SAVED_PHOTO.is_file():
        return SAVED_PHOTO.read_bytes(), "image/jpeg"
    return None, ""
