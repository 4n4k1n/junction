"""LearnLens API.

POST /sessions                    create a learning session (returns the first step's intro)
GET  /sessions/{id}               current step + state
POST /sessions/{id}/interact      multipart: image (optional), user_message, interaction_type
POST /sessions/{id}/confirm       manual confirmation of a physical result ("LED lights up")
GET  /sessions/{id}/events        structured log for debugging the demo
GET  /lessons/{id}                lesson content
GET  /                            browser test console (no glasses needed)
"""
from __future__ import annotations

import io
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import engine
from .lessons import all_lessons, get_lesson
from .models import (
    ConfirmRequest, CreateSessionRequest, InteractionType, Lesson, SessionState, TutorResponse,
)
from .store import Store
from .tutor import Tutor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("learnlens.api")

MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_MEDIA = {"image/jpeg", "image/png", "image/webp", "image/gif"}

app = FastAPI(title="LearnLens", version="0.1.0")
store = Store()
tutor = Tutor()


@app.on_event("startup")
def _warmup() -> None:
    if tutor.claude is not None:
        import threading
        threading.Thread(target=tutor.claude.warmup, daemon=True).start()


MAX_IMAGE_SIDE = int(os.getenv("LEARNLENS_MAX_IMAGE_SIDE", "1024"))


def downscale(data: bytes, media_type: str):
    """Shrink phone photos (e.g. 3000 px) before they go to the model: big latency win."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data, media_type
    try:
        im = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
        if max(im.size) <= MAX_IMAGE_SIDE and len(data) < 400_000:
            return data, media_type
        im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
        out = io.BytesIO()
        im.convert("RGB").save(out, format="JPEG", quality=85)
        return out.getvalue(), "image/jpeg"
    except Exception as e:
        log.warning("could not downscale image: %s", e)
        return data, media_type


def _load(session_id: str) -> SessionState:
    s = store.load(session_id)
    if s is None:
        raise HTTPException(404, "session not found")
    return s


@app.get("/health")
def health():
    return {"ok": True, "tutor": "claude" if tutor.claude else "mock"}


@app.get("/lessons")
def lessons():
    return [{"lesson_id": l.lesson_id, "title": l.title} for l in all_lessons().values()]


@app.get("/lessons/{lesson_id}", response_model=Lesson)
def lesson(lesson_id: str):
    try:
        return get_lesson(lesson_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/sessions", response_model=TutorResponse)
def create_session(req: Optional[CreateSessionRequest] = None):
    req = req or CreateSessionRequest()
    try:
        lesson_ = get_lesson(req.lesson_id)
    except KeyError as e:
        raise HTTPException(404, str(e))
    s = SessionState(session_id=uuid.uuid4().hex[:12], lesson_id=lesson_.lesson_id)
    resp = engine.intro(lesson_, s)
    store.save(s)
    store.log(s.session_id, "created", {"lesson_id": lesson_.lesson_id})
    return resp


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    s = _load(session_id)
    lesson_ = get_lesson(s.lesson_id)
    step = engine.current_step(lesson_, s)
    return {
        "state": s,
        "step": engine.step_view(lesson_, s),
        "check_question": step.check_question,
        "intro_display": step.intro_display,
    }


@app.post("/sessions/{session_id}/interact", response_model=TutorResponse)
async def interact(
    session_id: str,
    interaction_type: InteractionType = Form(InteractionType.question),
    user_message: str = Form(""),
    image: Optional[UploadFile] = File(None),
):
    s = _load(session_id)
    lesson_ = get_lesson(s.lesson_id)

    img_bytes, media_type = None, "image/jpeg"
    if image is not None and image.filename:
        img_bytes = await image.read()
        media_type = (image.content_type or "image/jpeg").lower()
        if media_type not in ALLOWED_MEDIA:
            raise HTTPException(415, f"unsupported image type {media_type}")
        if len(img_bytes) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "image larger than 5 MB – downscale on the phone")
        if interaction_type != InteractionType.photo:
            interaction_type = InteractionType.photo
        img_bytes, media_type = downscale(img_bytes, media_type)

    t0 = time.perf_counter()
    decision, source = tutor.decide(lesson_, s, interaction_type, user_message, img_bytes, media_type)
    llm_ms = int((time.perf_counter() - t0) * 1000)

    resp = engine.apply_decision(lesson_, s, interaction_type, user_message, decision, source)
    resp.latency_ms = int((time.perf_counter() - t0) * 1000)
    store.save(s)
    store.log(session_id, "interact", {
        "interaction_type": interaction_type.value, "user_message": user_message,
        "has_image": img_bytes is not None, "source": source, "llm_ms": llm_ms,
        "decision": decision.model_dump(), "response": resp.model_dump(),
    })
    log.info("session=%s step=%s type=%s source=%s action=%s advanced=%s latency=%dms",
             session_id, resp.step.step_id, interaction_type.value, source, resp.action.value,
             resp.advanced, resp.latency_ms)
    return resp


@app.post("/sessions/{session_id}/confirm", response_model=TutorResponse)
def confirm(session_id: str, req: Optional[ConfirmRequest] = None):
    s = _load(session_id)
    lesson_ = get_lesson(s.lesson_id)
    resp = engine.manual_confirm(lesson_, s)
    store.save(s)
    store.log(session_id, "confirm", {"note": (req.note if req else ""), "response": resp.model_dump()})
    return resp


@app.get("/sessions/{session_id}/events")
def events(session_id: str, limit: int = 50):
    _load(session_id)
    return store.events(session_id, limit)


@app.get("/")
def console():
    return FileResponse(Path(__file__).parent / "static" / "console.html")
