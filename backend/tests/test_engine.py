from app import engine
from app.lessons import get_lesson
from app.models import (
    InteractionType, SessionState, TutorAction, TutorDecision, VerificationStatus,
)

LESSON = get_lesson("led-basics")


def new_session(step_index=0):
    s = SessionState(session_id="t", lesson_id="led-basics", step_index=step_index)
    engine.intro(LESSON, s)
    return s


def d(**kw):
    base = dict(action=TutorAction.give_hint, display_text="x", spoken_text="x")
    base.update(kw)
    return TutorDecision(**base)


def test_clamp_display_limits_words():
    out = engine.clamp_display("one two three four five six seven eight nine ten eleven twelve thirteen")
    assert len(out.split()) == 12 and out.endswith("…")


def test_grade_answer_keywords():
    understand = LESSON.steps[1]
    assert engine.grade_answer(understand, "It limits the current so the LED doesn't burn")
    assert not engine.grade_answer(understand, "It looks nice")


def test_llm_cannot_advance_without_correct_answer():
    s = new_session(step_index=1)  # 'understand' – question step
    r = engine.apply_decision(LESSON, s, InteractionType.question, "hi",
                              d(next_step_allowed=True, answer_correct=None), "llm")
    # 'hi' has no concepts -> keyword grading says no, despite next_step_allowed=True
    assert not r.advanced and s.step_index == 1


def test_low_confidence_photo_is_not_verified():
    s = new_session()
    r = engine.apply_decision(LESSON, s, InteractionType.photo, "",
                              d(action=TutorAction.confirm_progress, confidence=0.4,
                                verification_status=VerificationStatus.verified), "llm")
    assert r.verification_status == VerificationStatus.uncertain
    assert r.action == TutorAction.request_image
    assert not s.visual_ok


def test_verified_photo_triggers_check_question():
    s = new_session()
    r = engine.apply_decision(LESSON, s, InteractionType.photo, "",
                              d(action=TutorAction.confirm_progress, confidence=0.9,
                                verification_status=VerificationStatus.verified, spoken_text="Nice."), "llm")
    assert s.visual_ok and s.question_pending
    assert r.action == TutorAction.ask_question
    assert r.display_text == LESSON.steps[0].check_question
    assert r.spoken_text == "Nice. " + LESSON.steps[0].check_question


def test_check_question_not_repeated_when_tutor_already_asked():
    s = new_session()
    r = engine.apply_decision(LESSON, s, InteractionType.photo, "",
                              d(action=TutorAction.ask_question, confidence=0.9,
                                verification_status=VerificationStatus.verified,
                                spoken_text="I see it all. Which leg goes to plus?"), "llm")
    assert r.spoken_text.count("?") == 1


def test_hint_requests_escalate_and_cap():
    s = new_session()
    seen = []
    for _ in range(5):
        r = engine.apply_decision(LESSON, s, InteractionType.hint, "", d(), "mock")
        seen.append(r.display_text)
    assert seen[:3] == [engine.clamp_display(h) for h in LESSON.steps[0].hint_levels]
    assert s.hint_level == 3


def test_manual_confirm_cannot_skip_understanding():
    s = new_session(step_index=1)
    r = engine.manual_confirm(LESSON, s)
    assert not r.advanced and r.action == TutorAction.ask_question


def test_manual_confirm_advances_build_step():
    s = new_session(step_index=2)
    r = engine.manual_confirm(LESSON, s)
    assert r.advanced and s.step_index == 3 and "build" in s.completed_steps
