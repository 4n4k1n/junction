# LearnLens demo script (3 minutes)

Two people: **Presenter** (wears the glasses, builds) and **Operator** (laptop: backend, agent, mirrored
phone screen on the projector). Start state: backend and agent running, app open, glasses connected,
board plugged in **with the sensor already wired once and unplugged again**, jumper wires within reach.

Timings are targets. If a step stalls for 10 seconds, use its fallback and keep talking.

| Time | Presenter says | Presenter does | What must appear | Fallback |
|---|---|---|---|---|
| 0:00 | "Hardware is hard to start with. You need to know pins, voltages and code. LearnLens is the friend who knows all three, in your glasses." | Holds up board and sensor | Glasses: "Tell your phone what you want to build." | – |
| 0:15 | "I want to build a clap-activated light." | Holds the talk button on the phone, or the goal is pre-typed | Goal text in the app | Type it |
| 0:25 | "It looks at what's on my desk." | Looks at the parts, taps **Capture with glasses and start** | "Capturing" → "Analyzing" | **Take a photo with the iPhone**, or **Pick a saved photo** |
| 0:40 | "It found the board. It isn't sure about the sensor, so it asks instead of guessing. And one thing it can't see from a photo: the pin labels. So I check." | Reads the labels on the sensor, taps **Yes** twice (glasses or phone) | Two questions: sensor present? labels AO, G, +, DO? | – |
| 1:00 | "The wiring comes from a hardware database built from the datasheets, not from the AI. This board is 3.3 volts. Wire the sensor to 5 and you can damage a pin, so it never suggests that." | Follows 5 steps: unplug USB, G→GND, +→+3V3, DO→D2, plug in. Taps **Done, next** each time | "Step n of 5" on glasses; the fact and its source on the phone | Operator reads steps from the phone |
| 1:45 | "Now Claude writes the code, for this exact wiring. It's checked against the approved pins and compiled for this board before I ever see it." | Waits | "Generating" → code on the phone, "Compiled for arduino:zephyr:unoq" | Restart in `--demo` (prevalidated sketch) |
| 2:05 | "Nothing touches my board until I say so." | Scrolls the code, taps **Approve and upload to the board** | "Deploying": validate, compile, approval, device, upload | If the upload fails: tap **Approve again and retry**; second failure → show the recorded video |
| 2:25 | "Uploaded. Let's see." | Claps once. Pause. Claps again. | Red LED on, then off. Phone: "Board log: SOUND_DETECTED, LED_ON" | If no log lines: "the LED is the proof" |
| 2:40 | "The board itself reports the clap." | Taps **Yes, it works** | Glasses: "Success" | – |
| 2:45 | "From an idea to working hardware in under three minutes, with every voltage checked and every upload approved. That's LearnLens." | – | – | – |

## Optional second build (45 seconds, if the first went well)

"Same parts, different idea." Say or type: **"Build a sound level meter."** The flow is the same, with
one different wire: sensor AO → A0 instead of DO → D2. After the upload the board's LED matrix shows a
bar that grows with the volume. Speak louder and quieter to show it. Note: after power-up the matrix
plays the board's boot animation for about half a minute first.

## Rules for the live run

- **Say which mode you are in.** If the app shows "DEMO MODE" or "SIMULATED upload", say so out loud:
  "the AI answers are saved for reliability; the board and the upload are real."
- Never claim the photo verified the wiring. The line is: "I confirm each step myself."
- Clap near the sensor, about 20 cm away. One clap, wait a second, one clap.
- Quiet room matters: set the sensor's trim pot on stage during setup, not at home.

## If things go wrong

| Problem | Do this |
|---|---|
| Glasses not connected | Turn on **Simulated glasses** in the app and mirror the phone. Say it. |
| No network to Claude | Operator restarts with `./scripts/start.sh --demo`. Banner shows DEMO MODE. |
| "No deploy agent connected" in the app | Operator checks the terminal running `start.sh` |
| Upload fails twice | Presenter: "Uploads over USB can be moody." Play the recorded clap video. |
| LED toggles by itself | Trim pot too sensitive: turn until the sensor's second LED is off in silence |
| LED reacts to silence, not claps | Tap **Regenerate for a sensor that is LOW on sound**, approve again |
