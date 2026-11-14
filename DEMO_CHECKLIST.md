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
| iOS app builds | verified with Xcode 27.2 (simulator and device) |
| Upload to the board | verified (reported working through the app) |
| Level meter on the LED matrix | **not verified** |
| Board log read back on the Mac | **not verified** |
| Claude vision on a real photo | **not verified** (no photo tested yet) |
| Claude writing the sketch | verified live through the proxy: both sketches passed the checks and compiled first try; not yet run on the board |
| iOS app on an iPhone with real glasses (registration, display, buttons, flow) | reported working by the team on 2026-11-14; which mode and which steps were covered is not recorded here |
| Three consecutive real-hardware runs | **not done** |

## Before demo day

- [x] Upload to the board over USB works (done through the app flow)
- [ ] Level meter on the board: the LED matrix bar follows the volume
- [ ] Board log: note which transport prints lines (`cli-monitor` or `adb-app-cli`), or that none does
- [ ] `ANTHROPIC_API_KEY` set; `backend/scripts/live_analyze.py` on a real desk photo names both parts
- [ ] One real `generate`: Claude's sketch passes the checks and compiles (`repairs_used` ≤ 1)
- [x] Xcode installed; app builds and runs
- [x] Real glasses: tested by the team, reported working (note here which steps: speech, photo, flash)
- [ ] Desk photo saved as `demo/desk.jpg` and in the iPhone photo library
- [ ] Backup video of the working Pong game recorded
- [ ] Three consecutive full runs logged below

## Three consecutive runs

Use `python3 scripts/demo_run.py` (or the app). A run counts only with a real board and real sound.

| Run | Mode (real / demo AI) | Upload ok | Matrix follows volume | Board log seen | Time | Notes |
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
- [ ] Board plugged in with a data cable; joystick and infrared sensor unwired; breadboard and seven jumper wires ready
- [ ] One full rehearsal run, then unwire again
- [ ] Phone on Do Not Disturb, screen lock off, volume up

## Manual hardware verification still needed

2. Pong and Snake are playable on the LED matrix, and the joystick directions are the right way round.
3. The KY-023 joystick powered from 3.3 V gives a usable signal on A0 and A1; the KY-032 on D2 restarts the game.
4. Reading `Monitor` output on the Mac.
5. Glasses: registration, display, buttons, photo capture while the display is active.
6. iPhone: push-to-talk with glasses connected, camera fallback, LAN access to the backend.
