# LearnLens

An AI hardware-building assistant for Meta Ray-Ban Display glasses. You say what you want to build,
the glasses show wiring steps for the parts on your desk, Claude writes the Arduino sketch, and after
your approval a local agent uploads it to the board.

Demo: *"I want to build a clap-activated light"* with an Arduino UNO Q and a KY-037 sound sensor.

```
Glasses ── iOS app ──HTTP──▶ Backend (FastAPI) ◀──polls── Deploy agent (Mac) ──USB──▶ Arduino UNO Q ── KY-037
                              Claude: sees parts, writes code       compiles, uploads after approval
                              Rules + hardware database: wiring, safety checks
```

## Start

One-time setup:

```bash
cd ~/Desktop/learnlens
python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt
CLI="/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"
"$CLI" core update-index && "$CLI" core install arduino:zephyr && "$CLI" lib install Arduino_RouterBridge
```

Every time (backend + deploy agent, Ctrl-C stops both):

```bash
cd ~/Desktop/learnlens
export ANTHROPIC_API_KEY=...      # only for a real-AI run
./scripts/start.sh                # real AI, real board
./scripts/start.sh --demo         # saved AI answers + prevalidated sketch, real board
./scripts/start.sh --demo --mock  # fully offline rehearsal: nothing real happens
```

The script prints the URL to enter in the app (`http://<mac-ip>:8000`).

Then either run the iPhone app (`ios/README.md`), or drive the same flow from a second terminal:

```bash
python3 scripts/demo_run.py                        # asks at each step; you type "approve" before the upload
python3 scripts/demo_run.py --photo demo/desk.jpg  # send a real photo
```

## Modes, and how to tell them apart

| Mode | AI | Board | How it is labelled |
|---|---|---|---|
| `./scripts/start.sh` | Claude | real upload | nothing extra |
| `--demo` | saved answers, prevalidated sketch | real upload | "DEMO MODE" on every glasses card and in the app; `analysis_source: demo`, `source: demo` |
| `--mock` | as chosen | no upload | "SIMULATED upload" in the app, on the glasses and in the log; `deploy.mock: true` |

`GET /health` reports `demo_mode`, `agent_connected` and `agent_mock`.

## Tests

```bash
cd ~/Desktop/learnlens/backend
.venv/bin/python -m pytest -q                      # backend (AI mocked)
.venv/bin/python -m pytest -q ../agent/tests       # deploy agent (tools and board mocked)
bash ../ios/typecheck/typecheck.sh                 # iOS sources typecheck (no Xcode needed)
bash ../ios/typecheck/contract_check.sh            # backend responses decode with the app's models
python3 ../agent/smoke_test.py                     # real toolchain + connected board; --flash uploads
```

## Where things are

| Path | What |
|---|---|
| `backend/app/build/` | sessions, analysis, wiring rules, code generation and checks, approval |
| `agent/` | deploy agent: compile, approval check, upload, board logs (`agent/README.md`) |
| `ios/` | SwiftUI app and Xcode project (`ios/README.md`) |
| `hardware/facts/` | hardware database with sources |
| `scripts/` | `start.sh`, `demo_run.py` |
| `DEMO_SCRIPT.md`, `DEMO_CHECKLIST.md` | the 3-minute demo |
| `docs/` | API contract, compile contract, architecture, plan, backend guide, earlier tutor concept |

## Status

Verified: backend flow with mocked AI, static checks, real compile for the UNO Q, board discovery on
USB, approval gating, offline rehearsal end to end. Not yet verified: upload and log read-back on the
board (run `agent/smoke_test.py --flash`), any live Claude call, and the iOS app in Xcode or on
glasses. Details: `DEMO_CHECKLIST.md`.
