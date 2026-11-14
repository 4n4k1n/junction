"""End-to-end walkthrough of the LED lesson against the API, offline (mock tutor)."""
import io

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100


def interact(sid, kind, msg="", image=False):
    files = {"image": ("photo.jpg", io.BytesIO(FAKE_JPEG), "image/jpeg")} if image else None
    r = client.post(f"/sessions/{sid}/interact",
                    data={"interaction_type": kind, "user_message": msg}, files=files)
    assert r.status_code == 200, r.text
    return r.json()


def test_full_led_lesson(monkeypatch):
    monkeypatch.setenv("LEARNLENS_MOCK_VISION", "verified")

    r = client.post("/sessions", json={"lesson_id": "led-basics"}).json()
    sid = r["session_id"]
    assert r["step"]["step_id"] == "identify"

    # 1 identify: photo verified -> check question -> answer
    r = interact(sid, "photo", image=True)
    assert r["action"] == "ask_question"
    r = interact(sid, "answer", "The longer leg goes to plus")
    assert r["advanced"] and r["step"]["step_id"] == "understand"

    # 2 understand: wrong answer gives a hint, right answer advances
    r = interact(sid, "answer", "it makes it pretty")
    assert r["action"] == "give_hint" and not r["advanced"] and r["step"]["hint_level"] == 1
    r = interact(sid, "answer", "It limits the current so the LED does not burn out")
    assert r["advanced"] and r["step"]["step_id"] == "build"

    # 3 build: manual confirm (LED lights up)
    r = client.post(f"/sessions/{sid}/confirm").json()
    assert r["advanced"] and r["step"]["step_id"] == "debug"

    # 4 debug
    r = interact(sid, "answer", "The LED was reversed, a diode only works in one direction")
    assert r["advanced"] and r["step"]["step_id"] == "explain"

    # 5 explain
    r = interact(sid, "answer", "Current flows in a closed loop, the resistor limits it and the long leg is on plus")
    assert r["lesson_complete"]

    state = client.get(f"/sessions/{sid}").json()["state"]
    assert state["completed_steps"] == ["identify", "understand", "build", "debug", "explain"]
    assert len(client.get(f"/sessions/{sid}/events").json()) >= 8


def test_unverifiable_photo_asks_for_manual_test(monkeypatch):
    monkeypatch.setenv("LEARNLENS_MOCK_VISION", "uncertain")
    sid = client.post("/sessions", json={}).json()["session_id"]
    r = interact(sid, "photo", image=True)
    assert r["verification_status"] == "uncertain" and not r["advanced"]


def test_rejects_non_image_upload():
    sid = client.post("/sessions", json={}).json()["session_id"]
    r = client.post(f"/sessions/{sid}/interact", data={"interaction_type": "photo"},
                    files={"image": ("x.txt", io.BytesIO(b"hi"), "text/plain")})
    assert r.status_code == 415


def test_unknown_session_404():
    assert client.get("/sessions/nope").status_code == 404
