# LearnLens deploy agent (macOS)

Compiles an approved sketch for the Arduino UNO Q and uploads it to a board on USB. Runs on the Mac
the board is plugged into. Standard library only: `python3 agent/learnlens_agent.py`.

## Verified workflow

Read from the installed tooling on 2026-11-14 (Arduino CLI 1.2.0 from Arduino IDE.app, core
`arduino:zephyr` 1.1.0, library `Arduino_RouterBridge` 0.4.4):

| Step | How | Status |
|---|---|---|
| Board id | FQBN `arduino:zephyr:unoq` (`arduino-cli board listall`) | verified |
| Compile | `arduino-cli compile --fqbn arduino:zephyr:unoq`, toolchain `arm-zephyr-eabi` | verified: the sample sketch compiles |
| Discovery | `arduino-cli board list --json`, match FQBN or USB VID 0x2341 / PID 0x0078 (`boards.txt`) | verified with a real board |
| Upload | `arduino-cli upload --fqbn arduino:zephyr:unoq -p <port>`. The core runs `remoteocd upload --adb-path <adb> -s <port serialNumber>`: OpenOCD on the board's Linux side, reached over USB with adb (`platform.txt`) | read from `platform.txt`; **never run** |
| Runtime logs | sketch prints through `Monitor` (Arduino_RouterBridge). Reading it on the Mac: `arduino-cli monitor -p <port>` (default) or `adb shell arduino-app-cli monitor` | **unverified**: a forum thread reports no working CLI-only method |

Arduino CLI is used, not App Lab: App Lab is a GUI (and `arduino-app-cli` runs on the board), while the
core's own upload recipe supports a host-side `arduino-cli upload` over USB. A network upload
(`remoteocd` over SSH with a password) also exists in the core; the agent does not implement it.

## Setup

```bash
CLI="/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"   # or brew install arduino-cli
"$CLI" core update-index
"$CLI" core install arduino:zephyr
"$CLI" lib install Arduino_RouterBridge
```

| Env var | Default | Purpose |
|---|---|---|
| `LEARNLENS_ARDUINO_CLI` | `arduino-cli` on PATH, else the one inside Arduino IDE.app | absolute path of the only compiler/uploader executable |
| `LEARNLENS_APPROVAL_SECRET` | key file `~/.learnlens/approval.key` (created by the backend, mode 0600) | shared secret for approval tokens; must match the backend |
| `LEARNLENS_AGENT_TOKEN` | – | if set: required as `X-Agent-Token` on local POSTs, and sent to the backend |
| `LEARNLENS_AGENT_PORT` | `8765` | local API port (always on 127.0.0.1) |
| `LEARNLENS_LOG_TRANSPORT` | `cli-monitor` | `cli-monitor`, `adb-app-cli` or `none` |
| `LEARNLENS_LOG_CAPTURE_S` | `6` | seconds of board output read after an upload |
| `LEARNLENS_BOARD_READY_TIMEOUT` | `90` | seconds to wait for a freshly plugged-in board to finish starting before an upload |

## Run

```bash
python3 agent/learnlens_agent.py                  # local API + backend polling (http://localhost:8000)
python3 agent/learnlens_agent.py --no-backend     # local API only
python3 agent/learnlens_agent.py --mock           # no tool, no board; every result is marked SIMULATED
```

With the backend: the user approves a sketch in the app → the backend signs an approval token and
queues a job → the agent claims it, compiles, uploads, and posts each stage to
`/agent/jobs/{id}/events` and the outcome to `/agent/jobs/{id}/result`. The app shows the stages
while it polls the build.

## Uploads right after plugging in

The UNO Q's serial port appears a few seconds after power-up, but uploads go through the board's Linux
side (adb), which needs longer to start: observed about 5 s after the port in the best case, and it can
sit in adb state `offline` for much longer. An upload in that window fails with "device offline".

The agent therefore asks `adb -s <serial> get-state` before uploading and waits until it says `device`
(event "the board is still starting up, waiting for it"). Halfway through the wait it runs
`adb reconnect offline` once. If the upload itself loses the board, it waits and retries once. If the
board never gets ready, the deploy fails at stage `device` with advice to replug and wait a minute.
`GET /device` shows each board's `adb_state`.

If a board stays `offline` for minutes: unplug it, wait five seconds, plug it back in. Restarting adb
(`~/Library/Arduino15/packages/arduino/tools/adb/*/adb kill-server`) also clears a stuck connection.

## Local API (127.0.0.1 only)

| Endpoint | Body | Returns |
|---|---|---|
| `GET /device` | – | `{connected, boards: [{port, serial_number, fqbn}], toolchain: {arduino_cli, core, ready}, problem, mock}` |
| `POST /compile` | `{build_id, sketch: {filename, code, sha256}, fqbn?}` | `{status: ok\|failed, diagnostics: [{file, line, column, severity, message}], log, sha256}` |
| `POST /flash` | `{build_id, approval_token, expected_events?}` | `{status: flashed, upload_ok, port, log, runtime_log, observed_events, logs_captured}` |
| `GET /logs?build_id=&since=&capture_s=` | – | `{events: [{stage, status, message, ts}], next, runtime_log, uploaded}` |

Errors are `{error, stage}` with: 422 invalid artifact, 409 not compiled or no board, 403 approval
missing/invalid/expired/used, 502 the upload tool failed, 401 bad agent token, 403 non-local caller.
`GET /logs` with `since=<next>` returns only new events; `capture_s` (max 15) reads the board again.

## Security

- **No shell.** Every process is started in `lldeploy/tools.py` from a fixed argument list with
  `shell=False`. The executable must be the resolved `arduino-cli` (or the core's `adb` for the
  optional log transport). The FQBN is a constant. The port comes from discovery and must look like
  `/dev/cu.*`. Nothing from a sketch, a model or a request becomes a command.
- **Upload needs approval.** `/flash` requires a token signed by the backend when the user approved:
  HMAC-SHA256 over build id, sketch sha256, expiry (5 minutes) and a nonce. It is checked against the
  sha256 of the sketch this agent compiled, and each token works once. A retry needs a new approval.
- **Workspace only.** Sketches are written to a fresh temp directory per agent run
  (`learnlens-agent-*/<build_id>/`), built with `--build-path` inside it, and deleted on exit. Build
  ids and filenames are pattern-checked and every path is verified to stay inside the workspace.
- **No host file access through the compiler.** Only `#include <Arduino_RouterBridge.h>` is accepted.
  Any other preprocessor line (including `#include "/some/file"`, digraphs and trigraphs) is rejected
  before a tool runs.
- **Local only.** The server binds 127.0.0.1 (not configurable), rejects requests whose `Host` is not
  localhost (DNS rebinding) or that carry an `Origin` header (web pages), and caps bodies at 64 KB.
  The agent reaches the backend by polling; the backend never needs to reach the agent.
- **Honest results.** `upload_ok` is true only when the upload tool exits with 0. Runtime log lines
  and `observed_events` contain only what was read from the board. `--mock` results carry `mock: true`
  and the text SIMULATED.

## Tests

```bash
backend/.venv/bin/python -m pytest agent/tests -q                         # subprocess and board mocked
LEARNLENS_REAL_COMPILE=1 backend/.venv/bin/python -m pytest agent/tests -q   # + real compile; skipped if a board is attached
```

## Real-hardware smoke test

Not yet performed: no UNO Q was connected when this was written. Run it once and record the results
in the table below.

1. Plug the UNO Q into the Mac with a USB-C data cable. Wait for Linux to boot (about a minute).
2. `python3 agent/smoke_test.py` → expect the toolchain lines, **one board with a `/dev/cu.*` port**,
   and "compiled". If `boards: none`, run `arduino-cli board list` and note what the board shows up as.
3. `python3 agent/smoke_test.py --flash` → type `flash` when asked. Expect "upload tool reported success"
   and the tool's own output. If it fails, the tool's message is printed; nothing is reported as flashed.
4. During the 20 s listening window, clap. Expect `READY`, `SOUND_DETECTED`, `LED_ON` / `LED_OFF`, and
   the red LED toggling. If "(nothing received)": retry with `LEARNLENS_LOG_TRANSPORT=adb-app-cli`.
5. Full path: start the backend and `python3 agent/learnlens_agent.py`, run the app flow, approve.

| Check | Result |
|---|---|
| Board listed by `GET /device` (port name, serial number) | works (2026-11-14): `/dev/cu.usbmodem22258956142`, serial `2225895614`, matched as `arduino:zephyr:unoq` |
| `arduino-cli upload` over USB succeeds | not run |
| Red LED3 off after upload, toggles on clap | not run |
| `Monitor` lines readable with `cli-monitor` | not run |
| `Monitor` lines readable with `adb-app-cli` | not run |
| KY-037 DO level while it hears sound (HIGH or LOW) | not run |
