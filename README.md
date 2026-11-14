# LearnLens

An AI hardware-building assistant for Meta Ray-Ban Display glasses. You say what you want to build,
the glasses tell you which parts you need and show the wiring steps, Claude writes the Arduino sketch
while you wire, and after your approval a local agent uploads it to the board.

Demo: *"A Pong game"* with an Arduino UNO Q and a KY-023 joystick, played on the board's 8×13 LED
matrix (VRx → A0, VRy → A1). No photo is needed to start; one can be taken as an extra check.

After a finished build the glasses offer three ways on:

- **Change this build**: same parts, new behaviour ("Snake instead"). No new wiring, new code, new approval.
- **Add a part**: "add the infrared sensor so the game restarts when I hold my hand over it". The part
  gets its own confirmation and wiring steps, then the sketch is regenerated for all parts.
- **Build something new**: back to the question, with a new session.

Offered projects are Pong and Snake. The hardware database knows the whole 41-part KY sensor kit; 22
of those parts can be added to a build, the others are refused with the reason. The earlier projects
(sound level meter, clap-activated light) are still in the code and its tests:
`LEARNLENS_OFFERED_PROJECTS=pong,snake,level_meter` offers the level meter again.

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
| `--demo` | no AI: prevalidated sketches for Pong and Snake, each also with a restart by the KY-032. Any other wish is refused with what works | real upload | "DEMO MODE" on every glasses card and in the app; `analysis_source: demo`, `source: demo` |
| `--mock` | as chosen | no upload | "SIMULATED upload" in the app, on the glasses and in the log; `deploy.mock: true` |

`GET /health` reports `demo_mode`, `agent_connected` and `agent_mock`.

## What keeps the flow short

- The sketch is written, checked and compiled in the background while you wire, and the glasses say so
  on the first wiring step. When the last wire is confirmed it is usually ready
  (`LEARNLENS_PREGENERATE=0` switches this off).
- The deploy agent compiles a sketch as soon as it waits for approval, so "Start flash" only uploads.
- A failed step offers **Retry** and keeps everything confirmed so far. Sessions and builds are saved
  to `backend/learnlens_state.json` and survive a backend restart.
- Steps are read aloud. Optional voice commands ("next", "back") during wiring are experimental.
- If the board reports a reaction in its log during the test (`HIT`, `FOOD`, …), the test confirms itself.

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
