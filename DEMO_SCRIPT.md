# LearnLens demo script (3 minutes)

Two people: **Presenter** (wears the glasses, builds) and **Operator** (laptop: backend, agent, mirrored
phone screen on the projector). Start state: backend and agent running, app open, glasses connected,
board plugged in **with the sensor already wired once and unplugged again**, jumper wires within reach.

Timings are targets. If a step stalls for 10 seconds, use its fallback and keep talking.

| Time | Presenter says | Presenter does | What must appear | Fallback |
|---|---|---|---|---|
| 0:00 | "Hardware is hard to start with. You need to know pins, voltages and code. LearnLens is the friend who knows all three, in your glasses." | Holds up board and sensor | Glasses: "Tell your phone what you want to build." | – |
| 0:15 | "Build a sound level meter." | Holds the talk button on the phone, or the goal is pre-typed | Goal text in the app | Type it |
| 0:25 | "It looks at what's on my desk." | Looks at the parts, taps **Capture with glasses and start** | "Capturing" → "Analyzing" | **Take a photo with the iPhone**, or **Pick a saved photo** |
| 0:40 | "It found the board. It isn't sure about the sensor, so it asks instead of guessing. And one thing it can't see from a photo: the pin labels. So I check." | Reads the labels on the sensor, selects **Yes** (glasses or phone) | Questions about the sensor and its labels AO, G, +, DO | – |
| 1:00 | "The wiring comes from a hardware database built from the datasheets, not from the AI. This board's analog pin takes 3.6 volts at most. Power the sensor from 5 and you can damage it, so it never suggests that." | Follows 3 steps: unplug USB and G→GND, +→+3V3, AO→A0 and plug USB in. Selects **Next** each time (each step is read aloud) | "Step n of 3" on glasses; the fact and its source on the phone | Operator reads steps from the phone |
| 1:45 | "Now Claude writes the code, for this exact wiring. It's checked against the approved pins and compiled for this board before I see it." | Usually no wait: the code was written while wiring | "Ready to flash" with a short description of how it works | Restart in `--demo` (prevalidated sketch) |
| 2:15 | "Nothing touches my board until I say so." | Selects **Start flash** on the glasses (the code is on the phone for anyone who wants to read it) | "Deploying": already compiled, approval, device, upload | If the upload fails: **Retry flash**; second failure → show the recorded video |
| 2:35 | "Uploaded. Let's hear it." | Speaks quietly, then loudly, then claps | The LED matrix bar grows with the volume | – |
| 2:50 | "From an idea to working hardware in three minutes, with every voltage checked and every upload approved. That's LearnLens." | Selects **Yes** | Glasses: "Success" | – |

Timing note: after the board gets power, the LED matrix plays the board's own boot animation for about
half a minute. Plug USB back in early (it is the last wiring step) so this is over before the upload.

## Second act: add the infrared sensor (about 90 seconds)

Works in `--demo` too: three ways to extend the meter have a prevalidated sketch, so no AI is needed.
You need a breadboard or a Y-cable, because both sensors share the board's single +3V3 pin.

| Presenter says | Presenter does | What must appear |
|---|---|---|
| "It works. Now let's keep building." | Selects **Continue building** | "Adding a part? Look at it and take a photo." |
| "Here is my second sensor." | Looks at the KY-032, selects **Take photo** | "Listening…" |
| "I want to add this sensor." | Speaks, then **Go** | "What should the KY-032 infrared obstacle sensor do in your build?" |
| "I have no idea yet. What could it do?" | Selects **Ideas** | Three ideas: show only while a hand is near, hold a hand to freeze, wave to switch on and off |
| "Let's switch it with a wave." | Selects the third idea | "Read the 4 pin labels on the infrared sensor…" → **Yes** |
| "Only the new wires. The first sensor stays as it is." | Steps 4-6: unplug USB and GND→GND, +→+3V3 (shared), OUT→D2 and plug USB in | "Step n of 6" |
| "New code for both sensors, and again nothing is uploaded without my OK." | **Start flash** | "Ready to flash" with the description, then "Deploying" |
| "Wave: off. Wave: on." | Waves a hand in front of the KY-032 | The level bar switches off and on |

In demo mode every follow-up request adds the infrared sensor, whatever is said, and the photo is
answered by a saved detection. In real mode Claude reads the request, looks at the photo and writes
the sketch, which takes 40-60 seconds.

## Rules for the live run

- **Say which mode you are in.** If the app shows "DEMO MODE" or "SIMULATED upload", say so out loud:
  "the AI answers are saved for reliability; the board and the upload are real."
- Never claim the photo verified the wiring. The line is: "I confirm each step myself."
- The meter calibrates itself in the first seconds. Start quiet, then get loud.

## If things go wrong

| Problem | Do this |
|---|---|
| Glasses not connected | Turn on **Simulated glasses** in the app and mirror the phone. Say it. |
| No network to Claude | Operator restarts with `./scripts/start.sh --demo`. Banner shows DEMO MODE. |
| "No deploy agent connected" in the app | Operator checks the terminal running `start.sh` |
| Upload fails twice | Presenter: "Uploads over USB can be moody." Play the recorded video. |
| Bar always full or always empty | Check AO → A0 and + → +3V3; unplug and replug USB to recalibrate |
| The app says it can only build a sound level meter | The goal was not understood as one. Say or type "Build a sound level meter". |
