# LearnLens demo script (about 4 minutes)

Two people: **Presenter** (wears the glasses, builds) and **Operator** (laptop: backend, agent, mirrored
phone screen on the projector). Start state: backend and agent running, app open, glasses connected,
board plugged in, joystick (KY-023), infrared sensor (KY-032), a breadboard and jumper wires within reach.

Timings are targets. If a step stalls for 10 seconds, use its fallback and keep talking.

## Act 1: Pong from a spoken goal (about 2:30)

| Time | Presenter says | Presenter does | What must appear | Fallback |
|---|---|---|---|---|
| 0:00 | "Hardware is hard to start with. You need to know pins, voltages and code. LearnLens is the friend who knows all three, in your glasses." | Holds up the board | Glasses: "What do you want to build today?" | – |
| 0:15 | "A Pong game." | **Speak**, says it, **Done**, then **Start** | "You want to build: a Pong game" | Type it on the phone |
| 0:25 | "It tells me what I need. No photo, no guessing." | Selects **Yes** | "You need: Arduino UNO Q, KY-023 joystick. Do you have them?" | – |
| 0:35 | "One thing only I can check: the labels on the part." | Reads the joystick's labels, selects **Yes** | "Read the 5 pin labels on the joystick. Are they GND, +5V, VRx, VRy and SW?" | – |
| 0:45 | "The wiring comes from a hardware database, not from the AI. This joystick says 5 volts on its pin, but this board's analog inputs take 3.6 at most. So it runs on 3.3, as the maker documents." | 4 steps: unplug USB and GND→GND, +5V→+3V3, VRx→A0, VRy→A1 and plug USB in. **Next** each time | "Step n of 4"; on step 1 also "writing the code meanwhile" | Operator reads the steps from the phone |
| 1:30 | "While I was wiring, Claude wrote the game for exactly these pins. It was checked against the approved pins and compiled before I see it." | Usually no wait | "Ready to flash" with how the game works | Wait: after a failed AI attempt the prepared sketch is used |
| 1:50 | "Nothing touches my board until I say so." | Selects **Start flash** | "Deploying" | **Retry flash**; second failure → recorded video |
| 2:10 | "Uploaded. Let's play." | Moves the joystick | Paddle on the left of the LED matrix, ball bouncing | If up and down are reversed, turn the joystick around |
| 2:25 | "It works." | Selects **Yes** | "It works. What next?" | – |

Timing notes: after the board gets power, the LED matrix plays the board's own boot animation for about
half a minute. Do not touch the joystick while the board starts: the sketch measures its resting position then.

## Act 2: Snake on the same wiring (about 45 seconds)

| Presenter says | Presenter does | What must appear |
|---|---|---|
| "Same parts, different game." | Selects **Change this build**, says "Snake instead", **Go** | "Generating", then "Ready to flash" |
| "New code, new approval." | **Start flash** | Snake on the matrix, food blinking |

## Act 3: restart with a hand (about 60 seconds)

You need a breadboard or a Y-cable, because both parts share the board's single +3V3 pin.

| Presenter says | Presenter does | What must appear |
|---|---|---|
| "Let's add a part." | Selects **Add a part**, takes the photo of the KY-032 or skips | "Listening…" |
| "Add the infrared sensor so the game restarts when I hold my hand over it." | Speaks, then **Go** | "Read the 4 pin labels on the infrared sensor…" → **Yes** |
| "Only the new wires. The joystick stays." | Steps 5-7: unplug USB and GND→GND, +→+3V3 (shared), OUT→D2 and plug USB in | "Step n of 7" |
| "And again nothing is uploaded without my OK." | **Start flash** | "Ready to flash", then "Deploying" |
| "Hand over the sensor: new game." | Holds a hand over the KY-032 | The game starts again |

## What runs in which mode

- **With the AI service** (`ANTHROPIC_API_KEY` set): any wish the known parts can do works, for example
  "a Breakout game" or "a light meter". Claude writes each sketch (about a minute; Act 1 hides most of
  it behind the wiring). If no attempt passes the checks, the prevalidated sketch for that game is used
  and the result says `source: template`.
- **Without it** (no key, no network, used-up budget): Pong, Snake and both with the hand restart come
  from prevalidated sketches, and so does the sound level meter. Any other wish is answered with
  what can be built. Any other change to a game (pause, speed, …) says that it needs the AI service.

## Rules for the live run

- **Say what is real.** If the app shows "No AI service" or "SIMULATED upload", say so out loud:
  "this code is a prepared sketch, not written live" or "the upload is simulated".
- Never claim a photo verified the wiring. The line is: "I confirm each step myself."

## If things go wrong

| Problem | Do this |
|---|---|
| Glasses not connected | Turn on **Simulated glasses** in the app and mirror the phone. Say it. |
| No network to Claude | Keep going: Pong, Snake and the restart use the prepared sketches (`source: template`). Say so. |
| "No deploy agent connected" in the app | Operator checks the terminal running `start.sh` |
| Upload fails twice | Presenter: "Uploads over USB can be moody." Play the recorded video. |
| Paddle or snake does not move | Check VRx → A0, VRy → A1 and +5V pin → +3V3; unplug and replug USB without touching the joystick |
| A direction is reversed | Turn the joystick around; left/right mirrored cannot be fixed that way, say so |
| The app says what it can build | The goal was not understood as Pong, Snake or the sound level meter. Say or type "Pong". |
| "I could not tell which part you mean" | The photo of the new part was not recognised. Name the part: "add the infrared sensor …". |
