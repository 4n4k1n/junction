from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict

from .models import Lesson

LESSON_DIR = Path(__file__).resolve().parent.parent / "lessons"


@lru_cache(maxsize=None)
def all_lessons() -> Dict[str, Lesson]:
    lessons = {}
    for path in sorted(LESSON_DIR.glob("*.json")):
        lesson = Lesson.model_validate(json.loads(path.read_text(encoding="utf-8")))
        lessons[lesson.lesson_id] = lesson
    return lessons


def get_lesson(lesson_id: str) -> Lesson:
    try:
        return all_lessons()[lesson_id]
    except KeyError:
        raise KeyError(f"unknown lesson '{lesson_id}'")
