# Demo checklist

## Verified versus simulated (2026-11-14)

| Part | State |
|---|---|
| Backend flow: session, part confirmation, wiring, safety checks | verified by 107 automated tests (AI mocked) |
| Static checks on generated code | verified by tests |
| Compile for the UNO Q (`arduino:zephyr:unoq`, real core) | verified |
| Board found on USB by the agent | verified (`/dev/cu.usbmodem…`) |
| Approval token, retry, local agent API | verified by 28 automated tests (tools mocked) |
| Launch script + full flow, three runs in a row | verified **in `--demo --mock`** (nothing real) |
| Backend responses decode with the iOS models | verified (16/16) |
| iOS sources typecheck | verified on macOS against stubs; **not built in Xcode** |
| Upload to the board | **not verified** |
| LED toggles on clap | **not verified** |
| Board log read back on the Mac | **not verified** |
| Claude vision on a real photo | **not verified** (no API key on this Mac) |
| Claude writing the sketch | **not verified**; tests use hand-written answers |
| iOS app on a phone, glasses display, glasses camera | **not verified** |
| Three consecutive real-hardware runs | **not done** |

## Before demo day

- [ ] `python3 agent/smoke_test.py --flash` succeeds; red LED toggles on a clap
- [ ] Board log: note which transport prints lines (`cli-monitor` or `adb-app-cli`), or that none does
- [ ] KY-037 trim pot set; note whether DO is HIGH or LOW on sound
- [ ] `ANTHROPIC_API_KEY` set; `backend/scripts/live_analyze.py` on a real desk photo names both parts
- [ ] One real `generate`: Claude's sketch passes the checks and compiles (`repairs_used` ≤ 1)
- [ ] Xcode 26.4+ installed; app builds; simulated-glasses flow works against the backend
- [ ] Real glasses: registered, display card visible, buttons work, photo capture works
- [ ] Desk photo saved as `demo/desk.jpg` and in the iPhone photo library
- [ ] Backup video of a successful clap recorded
- [ ] Three consecutive full runs logged below

## Three consecutive runs

Use `python3 scripts/demo_run.py` (or the app). A run counts only with a real board and a real clap.

| Run | Mode (real / demo AI) | Upload ok | LED toggled | Board log seen | Time | Notes |
|---|---|---|---|---|---|---|
| 1 | | | | | | |
| 2 | | | | | | |
| 3 | | | | | | |

## 30 minutes before

- [ ] Mac and iPhone on the same Wi-Fi (or the Mac's hotspot); note the Mac's IP
- [ ] `./scripts/start.sh` (or `--demo`) running; no red lines; "backend ready" and the agent's board line
- [ ] App: backend URL set, status "reachable", **no** red "No deploy agent" banner
- [ ] App banner matches what you intend to say (none / DEMO MODE / SIMULATED)
- [ ] Glasses charged, connected, display card visible; phone mirrored to the projector
- [ ] Board plugged in with a data cable; sensor unwired; three jumper wires ready
- [ ] One full rehearsal run, then unwire again
- [ ] Phone on Do Not Disturb, screen lock off, volume up

## Manual hardware verification still needed

1. Upload over USB with `arduino-cli upload` (remoteocd/adb).
2. `LED_BUILTIN` is the red LED3 and lights at LOW.
3. KY-037 at 3.3 V: detects a clap, and its output polarity.
4. Reading `Monitor` output on the Mac.
5. Glasses: registration, display, buttons, photo capture while the display is active.
6. iPhone: push-to-talk with glasses connected, camera fallback, LAN access to the backend.
