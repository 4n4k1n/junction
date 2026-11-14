"""Live smoke test against Claude (costs a few API calls).

Usage (from backend/):
    ANTHROPIC_API_KEY=... .venv/bin/python scripts/live_smoke.py [path/to/breadboard.jpg]

Runs the real ClaudeTutor (no mock fallback) through a few interactions and prints
each decision, how the engine applied it, and the latency.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("LEARNLENS_DB", os.path.join(tempfile.mkdtemp(), "smoke.db"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import engine  # noqa: E402
from app.lessons import get_lesson  # noqa: E402
from app.models import InteractionType, SessionState  # noqa: E402
from app.tutor import ClaudeTutor, MODEL, EFFORT  # noqa: E402


def run(tutor, lesson, s, kind, msg="", image=None, media_type="image/jpeg"):
    t0 = time.perf_counter()
    decision = tutor.decide(lesson, s, kind, msg, image, media_type)
    ms = int((time.perf_counter() - t0) * 1000)
    resp = engine.apply_decision(lesson, s, kind, msg, decision, "llm")
    print(f"\n── {kind.value}: {msg or '(photo)' if image else msg!r}  [{ms} ms]")
    print(f"   LLM   : action={decision.action.value} status={decision.verification_status.value} "
          f"conf={decision.confidence:.2f} answer_correct={decision.answer_correct}")
    if decision.visual_observation:
        print(f"   sees  : {decision.visual_observation}")
    print(f"   glass : {resp.display_text!r}  ({len(resp.display_text.split())} words)")
    print(f"   voice : {resp.spoken_text!r}")
    print(f"   engine: step={resp.step.step_id} hint={resp.step.hint_level} advanced={resp.advanced}")
    return ms


def main():
    print(f"model={MODEL} effort={EFFORT}")
    tutor = ClaudeTutor()
    lesson = get_lesson("led-basics")
    times = []

    # Step 'understand' (question-only) – exercises Socratic hinting + answer grading.
    s = SessionState(session_id="smoke", lesson_id="led-basics", step_index=1)
    engine.intro(lesson, s)
    times.append(run(tutor, lesson, s, InteractionType.question, "Why isn't my LED working? Just tell me the answer."))
    times.append(run(tutor, lesson, s, InteractionType.answer, "Because it looks nicer?"))
    times.append(run(tutor, lesson, s, InteractionType.answer, "The resistor limits the current so the LED doesn't burn out"))

    # Optional photo on step 'identify'.
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        photo_lesson = get_lesson(os.getenv("SMOKE_PHOTO_LESSON", "led-basics"))
        s2 = SessionState(session_id="smoke2", lesson_id=photo_lesson.lesson_id, step_index=0)
        engine.intro(photo_lesson, s2)
        times.append(run(tutor, photo_lesson, s2, InteractionType.photo, "", path.read_bytes(), media))
    else:
        print("\n(no image given – pass a breadboard photo to test vision)")

    print(f"\nlatency ms: {times}  max={max(times)}  target<5000")


if __name__ == "__main__":
    main()
