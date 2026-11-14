# Backend local development

## Setup

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q        # offline: no API key, no hardware
```

The venv on the team Mac is Python 3.9, so code uses `Optional[X]` and `List[X]`.

## Run

```bash
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` lets the phone reach it at `http://<mac-ip>:8000`. API docs are at `/docs`.

| Env var | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | – | Claude credentials. Stays on the backend, never in the app. |
| `ANTHROPIC_BASE_URL` | Anthropic API | the hackathon proxy, if used (then set `LEARNLENS_MODEL` to its model name) |
| `LEARNLENS_ANALYZER` | value of `LEARNLENS_TUTOR`, else `auto` | `auto` = Claude, or mock when no credentials; `claude` = Claude only; `mock` = offline |
| `LEARNLENS_MODEL` | `claude-opus-5-5` | model id |
| `LEARNLENS_ANALYZER_EFFORT` | `medium` | effort for the vision step |
| `LEARNLENS_DETECTION_THRESHOLD` | `0.8` | below this a detected part needs user confirmation |
| `LEARNLENS_MOCK_DETECTION` | `none` | `confident` makes the mock "see" the required parts (rehearsals only) |
| `LEARNLENS_AGENT_TOKEN` | – | if set, the deploy agent must send it as `X-Agent-Token` |
| `LEARNLENS_CODEGEN` | value of `LEARNLENS_TUTOR`, else `auto` | `auto` = Claude, or the template when no credentials; `claude`; `mock` = template only |
| `LEARNLENS_CODEGEN_EFFORT` | `medium` | effort for sketch generation |
| `LEARNLENS_ARDUINO_CLI` | PATH, else Arduino IDE.app | arduino-cli for the compile check; `none` disables it |
| `LEARNLENS_PROJECTS_DIR` | `backend/generated` | where generated sketches are saved |
| `LEARNLENS_APPROVAL_SECRET` | key file `~/.learnlens/approval.key` | signs deploy approval tokens; the agent needs the same |
| `LEARNLENS_APPROVAL_TTL` | `300` | seconds an approval stays valid |

## Try the flow by hand (mock analyzer, no key)

```bash
B=localhost:8000
SID=$(curl -s -H 'Content-Type: application/json' -d '{"instruction":"Build a clap-activated light"}' $B/sessions | python3 -c 'import sys,json;print(json.load(sys.stdin)["session_id"])')
curl -s -F image=@desk.jpg $B/sessions/$SID/analyze                  # mock sees nothing -> 3 questions
curl -s -F 'confirmed_inventory=[{"component_id":"uno-q"},{"component_id":"ky-037","confirmations":["ky037-pin-labels"]}]' $B/sessions/$SID/analyze
curl -s -H 'Content-Type: application/json' -d '{"step_index":0}' $B/sessions/$SID/confirm-step   # repeat for 1..4
curl -s $B/sessions/$SID/status
```

After the last step, generate the sketch (`curl -s -X POST $B/sessions/$SID/generate`, which returns
`build_id`). Then approve it and run the agent (`--mock` if no board is attached):

```bash
curl -s $B/builds/<build_id>          # read sketch.sha256
curl -s -H 'Content-Type: application/json' -d '{"sketch_sha256":"<sha>"}' $B/builds/<build_id>/approve
python3 ../agent/learnlens_agent.py --mock --once
```

For the compile check, install the board core once (see `agent/README.md`, Setup).

## Scripts

| Script | Does |
|---|---|
| `scripts/example_clap_session.py` | runs the whole clap-light flow with canned AI answers and rewrites `docs/examples/clap-light-session.json` |
| `scripts/live_analyze.py desk.jpg "…"` | one real Claude vision call on a photo (add `--review` for the wiring review); needs credentials |
| `scripts/example_generate.py` | generates the clap-light sketch and compiles it with the real core; `--repair` shows a compile error being repaired |
| `scripts/export_schemas.py` | regenerates `shared/schemas/build-api.schema.json` after a model change |

## Where things live

| File | Role |
|---|---|
| `hardware/facts/*.json` | hardware database: pins, voltage limits, LED polarity, facts with sources |
| `app/build/facts.py` | loads the database; `check_safety` runs the electrical rules |
| `app/build/analyzer.py` | Claude vision call, mock, and the fallback that detects nothing |
| `app/build/planner.py` | wiring steps from the database, no model involved |
| `app/build/codegen.py` | sketch generation: context, prompts, repair loop, template fallback |
| `app/build/codecheck.py` | static checks on a sketch |
| `app/build/compiler.py` | compile-only check with arduino-cli |
| `app/build/approval.py` | signs deploy approval tokens |
| `app/build/session_router.py` | session state machine and the four `/sessions` routes |
| `app/build/router.py` | builds, approval, deploy jobs |

## Adding hardware

1. Add `hardware/facts/<id>.json` with `role`, `identification` (appearance only), `pins` (each with
   `kind`, limits and a `fact` id), `facts` (each with `source` and `confidence`), and `confirmations`
   for anything only the user can check on the part. A sensor that can be added to a build also gets
   `add_on.signals` (the signal pins to wire). A pin without a printed label gets `show` and `say`
   (how the wiring step names it on the display and in speech). A part that is only recognised gets
   `not_buildable` (the reason, as told to the user), `add_on: null` and no pins.
2. Values come from the maker's documentation. If there is none, mark the fact `vendor_doc` or
   `secondary` and add a confirmation question.
3. Run the tests. `test_hardware_database_is_internally_consistent` checks the cross-references.

## Testing with mocked AI

`tests/test_sessions.py` swaps the Anthropic client for `FakeAnthropic` (in
`scripts/example_clap_session.py`), which returns queued JSON and records each request. To add a case,
queue the model answer you want to test and assert on the session state.
