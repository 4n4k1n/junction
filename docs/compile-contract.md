# Compile and deploy contract

Who compiles what, with which command, and what comes back. Toolchain facts were read from the
installed core `arduino:zephyr` 1.1.0 and checked by compiling the sample sketch.

## Toolchain

| Item | Value |
|---|---|
| Board | Arduino UNO Q, FQBN `arduino:zephyr:unoq` (not `arduino:avr:uno`) |
| Core | `arduino:zephyr` 1.1.0, compiler `arm-zephyr-eabi` |
| Library | `Arduino_RouterBridge` 0.4.4 (+ `Arduino_RPClite`, `MsgPack`), for `Monitor` |
| CLI | `arduino-cli` (1.2.0 ships inside Arduino IDE.app) |
| Sketch layout | `<dir>/learnlens_sketch/learnlens_sketch.ino` |
| Size of the sample | 87032 bytes flash (11%), 36988 bytes RAM (14%) |

UNO Q specifics the generator and the checks rely on:

- `Serial` is the hardware UART on D0/D1. Log output goes through `Monitor` (`Monitor.begin()`,
  `Monitor.println()`), routed via the board's Linux side.
- `LED_BUILTIN` is LED3 red (PH10) and is active-low.
- Upload is `remoteocd` over adb, not a serial bootloader.

## 1. Backend compile check (`POST /sessions/{id}/generate`)

Purpose: catch code that does not build, and feed the compiler's errors back to the model.

```
arduino-cli compile --fqbn arduino:zephyr:unoq --warnings default --no-color <project_dir>
```

- Runs only on sketches that passed the static checks.
- `project_dir` is `backend/generated/<session_id>/attempt-<n>/learnlens_sketch/` (git-ignored). Each
  attempt has its own directory, next to a `generation.json` with the context and the model's answer.
- The backend has no upload code path. `compiler.py` refuses to build a command that contains an
  upload flag or a port.

Result (`CompileReport`):

```json
{"status": "ok", "fqbn": "arduino:zephyr:unoq", "command": ["arduino-cli", "compile", "..."], "log": "Sketch uses 87032 bytes ...", "detail": ""}
```

| `status` | Meaning | Effect |
|---|---|---|
| `ok` | compiled | sketch goes to approval |
| `failed` | compiler error in the sketch | error lines go into a repair request (max 2 repairs), then the template |
| `not_run` | no `arduino-cli`, core or library on the backend machine, or timeout | sketch goes to approval uncompiled; `detail` says why; the agent compiles it later anyway |

## 2. Generation pipeline

```
ProjectSpec + confirmed wiring ──▶ CodegenContext (pins, LED level, Monitor, parameters; from the hardware database)
        │
        ▼
   Claude (JSON schema: source_code, explanation, assumptions, expected_serial_events)
        │                                              ▲ repair request with the problems
        ▼                                              │ (at most 2)
   schema ──▶ static checks ──▶ save ──▶ compile ──────┘
        │ all pass
        ▼
   Build in awaiting_approval          (no AI, or 3 failed attempts ──▶ rule-based template, same gates)
```

Static checks (`codecheck.py`): only approved pins, each as a named constant; sensor pin `INPUT` and
never written; only `#include <Arduino_RouterBridge.h>` and no other preprocessor line; no loops,
`delay` over 50 ms, interrupts, analog I/O, dynamic memory, registers, HAL or Zephyr calls, Bridge RPC,
`Serial` or other buses; `SENSOR_ACTIVE_LEVEL` and `DEBOUNCE_MS` declared with the confirmed values and
used; `millis()` used; `Monitor.begin`; the four log lines present.

The level meter project (`behavior: level_meter`) changes the contract: the signal is the sensor's
analog output on A0 (`analogRead` on that pin only, no digital pin access at all), the output is the
LED matrix through `<Arduino_LED_Matrix.h>`, `for` and `while` loops are allowed, the required
parameter is `SAMPLE_WINDOW_MS` (request field `sample_window_ms`, 5..100), and the only required log
line is `READY`.

Not checked statically: whether the edge-detection logic is correct. The prompt requires it, the
template implements it, and the user sees the code before approving.

`GenerationResult` (full example: `docs/examples/clap-light-generation.json`):

| Field | Meaning |
|---|---|
| `source` | `llm` or `template` |
| `sketch` | `{filename, code, sha256, provenance}` |
| `explanation`, `assumptions`, `expected_serial_events` | from the model (or the template) |
| `parameters` | `SENSOR_ACTIVE_LEVEL`, `DEBOUNCE_MS`, pin, LED level, FQBN actually used |
| `attempts` | each attempt's issues and compile report |
| `repairs_used` | 0 to 2 |
| `compile` | the final attempt's `CompileReport` |
| `flashed`, `hardware_verified` | always `false`: generation uploads nothing and proves nothing about hardware |

Request body (optional): `{"sensor_active_level": "HIGH"|"LOW", "debounce_ms": 20..2000}`. Calling
`generate` again before approval replaces the sketch, for example to flip the sensor polarity.

## 3. Deploy agent

After `POST /builds/{id}/approve`, the backend queues a `DeployJob`:

```json
{"job_id": "…", "build_id": "…", "fqbn": "arduino:zephyr:unoq",
 "sketch": {"filename": "learnlens_sketch.ino", "code": "…", "sha256": "…", "provenance": "llm"},
 "approval_token": "<base64url payload>.<hmac>", "expected_serial_events": ["READY", "SOUND_DETECTED", "LED_ON", "LED_OFF"]}
```

The agent (`agent/README.md`) validates the artifact, compiles it again in its own workspace, checks
the approval token, finds the board, uploads, and reads back log output:

```
arduino-cli compile --fqbn arduino:zephyr:unoq --warnings default --no-color --build-path <ws>/<build>/build <ws>/<build>/learnlens_sketch
arduino-cli upload  --fqbn arduino:zephyr:unoq -p <discovered port> --input-dir <ws>/<build>/build --no-color <ws>/<build>/learnlens_sketch
```

While it works it posts `DeployEvent`s (`{stage, status, message, ts}`) to `/agent/jobs/{id}/events`;
they appear in `Build.deploy_events`. The final `DeployResult`:

```json
{"compile_ok": true, "upload_ok": false, "mock": false, "failed_stage": "device",
 "log": "no Arduino UNO Q found on USB. Plug it in with a data cable and retry.",
 "diagnostics": [], "port": "", "runtime_log": [], "observed_events": []}
```

- `upload_ok` is true only when the upload tool exited with 0.
- `runtime_log` / `observed_events` list only what was read from the board.
- A failed deploy leaves the build `failed`; approving again (`POST /builds/{id}/approve`) signs a new
  token and retries. A build whose behavior test failed cannot be re-approved.

## Not verified

No board was connected and no Claude call was made from this Mac. Unverified: upload, board discovery
output, log read-back, LED behavior, sensor polarity, and what Claude actually returns for the prompt.
The sample in `docs/examples/clap-light-generated.ino` is the template's output, and the "AI answers"
in the tests are hand-written.
