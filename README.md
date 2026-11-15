# LearnLens

An AI hardware-building assistant for Meta Ray-Ban Display glasses. You say what you want to build,
the glasses tell you which parts you need and show the wiring steps, Claude writes the Arduino sketch
while you wire, and after your approval a local agent uploads it to the board.

```
Glasses ── iOS app ──HTTP──▶ Backend (FastAPI) ◀──polls── Deploy agent (Mac) ──USB──▶ Arduino UNO Q ── KY parts
                              Claude: proposes parts, writes code   compiles, uploads after approval
                              Rules + hardware database: wiring, safety checks
```

## What you can build

Say the goal; no photo is needed. A photo can be taken as an extra check of the parts.

| Wish | Parts | Without the AI service |
|---|---|---|
| "A Pong game", "Snake" | Arduino UNO Q, KY-023 joystick (VRx → A0, VRy → A1) | works: prevalidated sketch |
| "A sound level meter" | Arduino UNO Q, KY-037 microphone (AO → A0) | works: prevalidated sketch |
| Anything else the known parts can do, e.g. "a Breakout game", "a light meter", "a reaction game with the button" | Claude proposes up to three parts from the hardware database | refused, with what works |

The only outputs are the board's 8×13 LED matrix and its LED. A wish that needs sound, a motor, a
relay or an external LED is refused with the reason.

The hardware database knows the whole 41-part KY sensor kit. 23 parts have wiring rules and can be
built with. The other 18 are recognised and refused with the reason: output parts, parts that need
an extra library or a bus, and parts the vendor specifies for 5 V.

After a finished build the glasses offer three ways on:

- **Change this build**: same parts, new behaviour ("Snake instead"). No new wiring, new code, new approval.
- **Add a part**: "add the infrared sensor so the game restarts when I hold my hand over it". A photo
  of the part can say which part "this sensor" is. The part gets its own confirmation and wiring
  steps, then the sketch is regenerated for all parts.
- **Build something new**: back to the question, with a new session.

Switching between Pong and Snake and adding the KY-032 as a restart also work without the AI
service. Any other change is written by Claude and needs it.

## What stays fixed, whatever is asked

- Claude only proposes which known parts a project needs and what it should do. Pins, voltages and
  wiring come from `hardware/facts/` through the planner, and every plan passes the safety check
  before it is shown.
- Each part's pin labels are confirmed on the physical part before wiring.
- Generated code passes the static checks (approved pins and includes only) and a real compile before
  it is shown.
- Nothing is uploaded without the explicit approval of that sketch.

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
export ANTHROPIC_API_KEY=...      # the AI service; never commit it. Without it: prevalidated projects only
./scripts/start.sh                # real board
./scripts/start.sh --mock         # simulated upload: no board needed, nothing is flashed
```

With a key from Anthropic directly, make sure no proxy settings are left in the terminal:
`unset ANTHROPIC_BASE_URL LEARNLENS_MODEL`. With a LiteLLM-style proxy, set `ANTHROPIC_BASE_URL` to
its address and `LEARNLENS_MODEL` to the model name it uses.

The script prints the URL to enter in the app (`http://<mac-ip>:8000`). Restart it after pulling new
code: the backend does not reload by itself.

Then either run the iPhone app (`ios/README.md`), or drive the same flow from a second terminal:

```bash
python3 scripts/demo_run.py                          # Pong; asks at each step; you type "approve" before the upload
python3 scripts/demo_run.py --goal "Build a sound level meter."
python3 scripts/demo_run.py --photo desk.jpg         # send a photo as an extra check
```

## What a run is, and how to tell

| Situation | What happens | How it is labelled |
|---|---|---|
| `ANTHROPIC_API_KEY` set | Claude proposes parts and writes the code | `source: llm` |
| No key, or the AI service fails | Pong, Snake (each also with a restart by the KY-032) and the sound level meter are built from prevalidated sketches. A free wish is refused, and a free change to a build says that it needs the AI service | banner in the app when no AI is configured; `analysis_source: mock` or `fallback`, `source: template` |
| `--mock` | the upload is simulated, no board is flashed | "SIMULATED upload" in the app, on the glasses and in the log; `deploy.mock: true` |

There is no separate demo mode. `GET /health` reports `ai_available`, `agent_connected`, `agent_mock`
and `ai_error`: why the last call to the AI service failed (for example a used-up budget), empty when
it worked.

## Settings

| Variable | Default | What it does |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | turns the real AI on |
| `ANTHROPIC_BASE_URL`, `LEARNLENS_MODEL` | Anthropic, `claude-opus-5-5` | only for a proxy |
| `LEARNLENS_OFFERED_PROJECTS` | `pong,snake,level_meter` | the projects with a prevalidated sketch that are offered (`toggle_on_event` adds the old clap light) |
| `LEARNLENS_FREE_PROJECTS` | `1` | `0`: only the offered projects, no free wishes |
| `LEARNLENS_PREGENERATE` | `1` | `0`: write the code after the wiring, not during it |
| `LEARNLENS_LLM_TIMEOUT` | `20` | seconds to wait for the AI when it reads a request or a photo |
| `LEARNLENS_PORT`, `LEARNLENS_AGENT_PORT` | `8000`, `8765` | ports of the backend and the deploy agent |

## What keeps the flow short

- The sketch is written, checked and compiled in the background while you wire, and the glasses say so
  on the first wiring step. Writing takes about a minute; when the last wire is confirmed it is
  usually ready.
- A game named in plain words ("Pong", "Snake instead") is recognised without a model call.
- The deploy agent compiles a sketch as soon as it waits for approval, so "Start flash" only uploads.
- A failed step offers **Retry** and keeps everything confirmed so far. Sessions and builds are saved
  to `backend/learnlens_state.json` and survive a backend restart.
- Steps are read aloud. Optional voice commands ("next", "back") during wiring are experimental.
- If the board reports a reaction in its log during the test (`HIT`, `FOOD`, `LOUD`, …), the test
  confirms itself.

## Tests

```bash
cd ~/Desktop/learnlens/backend
.venv/bin/python -m pytest -q                      # backend (AI mocked)
LEARNLENS_REAL_COMPILE=1 .venv/bin/python -m pytest -q   # also compiles every prevalidated sketch for the UNO Q
.venv/bin/python -m pytest -q ../agent/tests       # deploy agent (tools and board mocked)
bash ../ios/typecheck/typecheck.sh                 # iOS sources typecheck (no Xcode needed)
bash ../ios/typecheck/contract_check.sh            # backend responses decode with the app's models
python3 ../agent/smoke_test.py                     # real toolchain + connected board; --flash uploads
```

## Where things are

| Path | What |
|---|---|
| `backend/app/build/` | sessions, project proposal, wiring rules, games, code generation and checks, approval |
| `agent/` | deploy agent: compile, approval check, upload, board logs (`agent/README.md`) |
| `ios/` | SwiftUI app and Xcode project (`ios/README.md`) |
| `hardware/facts/` | hardware database with sources: the board and the 41 KY modules |
| `scripts/` | `start.sh`, `demo_run.py` |
| `DEMO_SCRIPT.md`, `DEMO_CHECKLIST.md` | the demo in three acts: Pong, Snake, restart by hand |
| `docs/` | API contract, compile contract, architecture, plan, backend guide, earlier tutor concept |

`git checkout level-meter-version` shows the last version before the KY kit and the goal-first flow.

## Status

Verified by tests and tools: the backend flow with mocked AI, the static checks, a real compile of
every prevalidated sketch for the UNO Q, approval gating, and the offline flow from the terminal up
to the approval.

Verified in live runs against the Claude API, each sketch really compiled: Pong, Snake, the restart
by the KY-032, the change from Pong to Snake, and three free wishes (Breakout with the rotary
encoder, a light meter, a reaction game with the button).

Reported from use with the glasses and the board: the build flow on the glasses, uploads to the
board, and the Pong flow. The deploy agent has read log lines from the board after uploads.


Details: `DEMO_CHECKLIST.md`.
