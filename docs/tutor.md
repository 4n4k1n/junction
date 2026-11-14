# Socratic tutor (earlier concept)

This was the project's first direction. The code still runs and its tests pass. Its routes moved from
`/sessions` to `/tutor/sessions` when the build assistant took over `/sessions`. The text below is the
original README with the routes updated.

---

# LearnLens

Socratic AI tutor for Meta Ray-Ban Display. **See → Teach → Verify.**
Hackathon goal: a beginner builds a working LED circuit with LearnLens as the only help.

```
Glasses (camera · display · audio)
   │
iOS companion app  ── ios/            hardware + rendering only
   │  multipart HTTP
FastAPI backend    ── backend/app/
   ├─ tutor.py     Claude (vision + structured JSON) → TutorDecision, falls back to MockTutor
   ├─ engine.py    deterministic state machine: hints, check questions, advancing
   ├─ store.py     SQLite: session state + event log
   └─ lessons/     JSON lesson definitions (led-basics.json)
```

**Core rule:** the LLM only *proposes* a `TutorDecision`. `engine.py` decides whether the learner
advances. Low-confidence photos never count as verified, and a step that tests understanding can't be
skipped with a manual confirm.

## Run the backend

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 for the **test console**: a 600×600 glasses-display preview plus
photo upload, text input, hint and confirm buttons. You don't need the glasses for it.

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Claude credentials (or `ant auth login`), or the hackathon proxy key |
| `ANTHROPIC_BASE_URL` | Anthropic API | hackathon LiteLLM proxy: `https://ai.42hn.dev` (then set `LEARNLENS_MODEL=opus-5.5`) |
| `LEARNLENS_TUTOR` | `auto` | `auto` = Claude with mock fallback, `claude` = Claude only, `mock` = offline |
| `LEARNLENS_MODEL` | `claude-opus-5-5` | model ID |
| `LEARNLENS_EFFORT` | `low` | effort level; low keeps time-to-hint short |
| `LEARNLENS_MOCK_VISION` | `uncertain` | `verified` makes mock photos pass (rehearsals) |
| `LEARNLENS_DB` | `backend/learnlens.db` | SQLite path |

Tests (offline, mock tutor, full 5-step lesson walkthrough):

```bash
cd backend && .venv/bin/python -m pytest -q
```

## API

| Endpoint | Purpose |
|---|---|
| `POST /tutor/sessions` `{lesson_id}` | new session → intro of step 1 |
| `GET /tutor/sessions/{id}` | state, current step, check question |
| `POST /tutor/sessions/{id}/interact` | multipart: `interaction_type` (photo/question/answer/hint), `user_message`, `image?` |
| `POST /tutor/sessions/{id}/confirm` | manual confirmation ("LED lights up") |
| `GET /tutor/sessions/{id}/events` | structured log incl. LLM decision + latency |
| `GET /lessons/{id}` | lesson content |
| `GET /health` | `{tutor: claude|mock}` |

Response (`TutorResponse`):

```json
{
  "session_id": "a1b2c3", "action": "give_hint",
  "display_text": "Where should the resistor go?",
  "spoken_text": "Think about how current flows.",
  "step": {"step_id": "build", "step_index": 2, "total_steps": 5, "title": "Build the circuit", "hint_level": 2, "max_hints": 3},
  "verification_status": "uncertain", "next_step_allowed": false, "advanced": false,
  "lesson_complete": false, "visual_observation": "LED and resistor visible", "confidence": 0.62,
  "source": "llm", "latency_ms": 2400
}
```

`action` ∈ `give_hint | ask_question | request_image | confirm_progress | explain`.
`source` ∈ `llm | fallback | mock | engine`. Show `fallback` on the demo dashboard.

## Lesson flow (led-basics)

| Step | Verification | Done when |
|---|---|---|
| identify | vision_and_question | photo verified (confidence ≥ 0.7) **and** "Which LED leg connects to plus?" answered |
| understand | question | resistor purpose explained (current + limit) |
| build | visual_and_manual | (photo verified + "Does it light?") **or** `/confirm` |
| debug | question | explains the reversed LED |
| explain | question | explains current path + resistor + direction |

Add a lesson by dropping another JSON file into `backend/lessons/`.

## Team split (one folder each, to avoid merge conflicts)

| Person | Owns | First milestone |
|---|---|---|
| 1 Wearables & iOS | `ios/` | glasses photo → `/interact` → text on glasses (by hour 4) |
| 2 Backend & Vision | `backend/app/tutor.py`, `main.py` | real photos of your breadboard get correct `visual_observation` |
| 3 Learning Engine | `backend/app/engine.py`, `backend/lessons/` | lesson playable end-to-end in the console |
| 4 UX & Demo | `backend/app/static/`, `docs/` | display layouts, mirrored demo screen, pitch |

## Known open points
- Live path verified through the proxy (`opus-5.5`, effort low): text turns 2.4–3.3 s, photo 4.1 s (synthetic image).
  Still to do: real breadboard photos from the glasses camera, especially wiring/polarity judgements.
- Latency target is under 5 s after capture. Measure `latency_ms` and lower image size or effort if it's slower.
- Voice input/output lives in the iOS app (on-device STT/TTS). The backend only sees text.
