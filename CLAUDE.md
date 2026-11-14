# LearnLens

Hackathon project (48 h). Two flows share one FastAPI backend:

- **Build assistant** (current focus): spoken goal → parts list, user confirms the parts → wiring steps on Meta
  Ray-Ban Display, each confirmed → user approves a generated Arduino sketch → Mac agent
  compiles/uploads → user reports the test result.
  Demo: "A Pong game" with an Arduino UNO Q and a KY-023 joystick, then Snake, then a KY-032 to restart.
- **Tutor** (earlier work, keep it passing): Socratic lessons under `/tutor/sessions`,
  `backend/app/{engine,tutor,store}.py`.

Read `docs/architecture.md`, `docs/api-contracts.md` and `docs/plan.md` before larger changes.
Backend how-to: `docs/backend-dev-guide.md`. iOS: `ios/README.md`.

## Commands

Start everything: `./scripts/start.sh` (`--demo` = saved AI answers, `--mock` = simulated upload).
Drive the flow from a terminal: `python3 scripts/demo_run.py`. Demo material: `DEMO_SCRIPT.md`,
`DEMO_CHECKLIST.md`.

```bash
cd backend
.venv/bin/python -m pytest -q                      # all tests, offline
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/python scripts/export_schemas.py         # after changing app/build/models.py
.venv/bin/python scripts/example_clap_session.py   # full flow with canned AI answers
.venv/bin/python -m pytest ../agent/tests -q       # deploy agent tests (subprocess mocked)
python3 ../agent/learnlens_agent.py --mock         # deploy agent, no hardware
python3 ../agent/smoke_test.py                     # real toolchain check; --flash uploads after typed approval
bash ../ios/typecheck/typecheck.sh                 # typecheck the iOS app without Xcode
```

The venv is Python 3.9: use `Optional[X]` / `List[X]`, not `X | None` / `list[X]`.

## Rules

1. A working end-to-end demo beats features. Every phase ends with a demo that runs.
2. Meta SDK: before writing any DAT code, read the matching `mwdat-ios:*` skill and the samples in
   `~/.claude/plugins/marketplaces/mwdat-ios-marketplace/samples/` (DisplayAccess, CameraAccess), or
   query `search_dat_docs`. Never write an SDK symbol you have not seen there.
3. Hardware facts live only in `hardware/facts/*.json`, each with a source and a confidence.
   Never put a pin name, voltage or board identifier anywhere else from memory. To add one, add a
   fact with its source first. `unverified` means measure it on the bench.
4. The model only proposes which known parts are in the photo and what the project is. Wiring and
   the sketch are built by rules from the hardware database (`planner.py`) and must pass
   `facts.check_safety` before they reach the user. Do not let model text into pins, voltages or code.
   A photo review is never an electrical verification.
5. Flashing needs explicit approval. Only `POST /builds/{id}/approve` with the sketch's sha256 creates
   a deploy job and signs the short-lived approval token the agent requires. Do not add another path
   to a job or to a token. The backend compiles but has no upload code.
   Generated code must pass `codecheck.check_sketch` before it is compiled or shown.
   The agent starts processes only in `agent/lldeploy/tools.py`, from fixed argument lists, never
   through a shell.
   Never report an upload, a log line or a hardware result that a tool did not actually produce.
   The UNO Q is not a classic UNO: FQBN `arduino:zephyr:unoq`, logs via `Monitor`, LED active-low.
6. API keys stay in the backend environment. Nothing secret in `ios/` or `agent/`.
7. No hardware or API key → mock it (`MockAnalyzer`, `MockRunner`, MockDeviceKit) and say so in the
   response (`analysis_source: mock`, `deploy.mock: true`).
8. If a part's variant or pin mapping is uncertain, the session stops and asks the user
   (`confirmations` in the hardware database). Do not weaken that to make a demo smoother.

## Layout

```
backend/app/build/   build assistant: models, hardware db + safety, analyzer, planner, session + build routers
backend/app/         tutor flow + main.py (mounts both)
backend/tests/       pytest, offline
agent/               Mac deploy agent (stdlib only): lldeploy/, tests/, smoke_test.py, README.md
hardware/facts/      hardware database (JSON): pins, limits, facts with sources
shared/schemas/      generated JSON Schema of the build API (do not edit by hand)
ios/                 SwiftUI app (LearnLens/), Xcode project, typecheck harness
docs/                architecture, API contracts, plan
```
