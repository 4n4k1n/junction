"""Prevalidated ways to develop the sound level meter further with the infrared sensor.

Each has a sketch that is known to pass the checks, so these work without the AI service, and they
are the ideas offered when there is none.
"""
from __future__ import annotations

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
