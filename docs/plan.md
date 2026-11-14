# Implementation plan and task list

Status on 2026-11-14: the whole flow runs with mocked AI: analysis, part confirmation, guided wiring,
code generation with static checks, and a real compile for the UNO Q. The deploy agent compiles for
real and is ready to upload, but no board has been connected yet. The iOS app is written but has not
been built with Xcode. No live Claude call has been made.

## Phases (each ends with a runnable demo)

| Phase | Goal | Demo at the end |
|---|---|---|
| 0 Scaffold (done) | contracts, mock backend, mock agent, tests | curl + `agent --mock`: goal → wiring → approve → "flashed" → done |
| 1 Real flashing (compile verified, upload untested) | agent compiles and uploads a real sketch | approve in the app, board runs the clap sketch |
| 2 Analysis + guidance (done, mocked AI) | Claude detects parts; rules plan the wiring; steps confirmed one by one | `scripts/example_clap_session.py` |
| 3 Glasses UI (written, unbuilt) | iOS app drives the flow, wiring steps on the display | full demo on MockDeviceKit preview, then real glasses |
| 4 Hardening | failure paths, rehearsal, fallback video | demo survives no wifi / no glasses |

Phases 1 and 3 need different people and hardware and can run in parallel.

## Tasks, in priority order

**P0 – first contact with real things (each can sink the demo)**
1. Board: run the smoke test in `agent/README.md` (`smoke_test.py`, then `--flash`). It settles board
   discovery, upload over USB, the LED, and how `Monitor` output is read. Fill in its results table.
2. Sensor: KY-037 on +3V3, power LED on, set the trim pot, note whether DO is HIGH or LOW on a clap.
   If LOW, regenerate with `sensor_active_level: LOW` and update `ky037-outputs`.
3. Claude: set `ANTHROPIC_API_KEY`, run `scripts/live_analyze.py` on a real desk photo and one
   `POST /sessions/{id}/generate`. Check that the generated sketch passes the checks on the first try.
4. Xcode 26.4+: open `ios/LearnLens.xcodeproj`, fix what the first build reports, run the simulated flow.

**P1 – demo path**
5. Work through `ios/README.md`'s hardware checklist on real glasses (registration, display, camera).
6. Use the log lines the agent reads back (`observed_events`) as an automatic test signal in the app.
7. Tune the detection threshold and the vision prompt on 10-20 real photos.

**P2 – demo quality**
8. Persist sessions and builds in SQLite (reuse `app/store.py`).
9. On a failed behavior test: guided re-check of the wiring, then regenerate.
10. Network upload (the core supports `remoteocd` over SSH) for a board that is not on USB.
11. More sensors in the hardware database.

**Cut if short on time:** 6, 8-11, voice input (type the goal instead).
