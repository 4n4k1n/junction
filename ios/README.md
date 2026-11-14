# LearnLens iOS app

SwiftUI iPhone app for Meta Ray-Ban Display, built on the Meta Wearables Device Access Toolkit 1.0.1
(`MWDATCore`, `MWDATDisplay`, `MWDATCamera`, `MWDATMockDevice`). It follows the official DisplayAccess,
CameraAccess and BirdSpotter samples.

**Status (2026-11-14): builds with Xcode 27.2**, for the iOS Simulator and for a physical iPhone
(unsigned), with no errors or warnings in the app's sources. The hand-written project file loads and
the SDK package resolves at 1.0.1. It launches in the iPhone 18 Pro simulator (iOS 27.2), reaches the
backend and shows the demo-mode and simulated-upload banners. **Not yet exercised:** tapping through
the flow, simulated glasses, a real iPhone, and real glasses.

`typecheck/typecheck.sh` still works without Xcode, and `typecheck/contract_check.sh` checks that
backend responses decode with the app's models.

## Build

Needs Xcode 26.4+ (Swift 6.3), iOS 17.2+.

```bash
open ios/LearnLens.xcodeproj        # Xcode resolves the meta-wearables-dat-ios package (exact 1.0.1)
# or
xcodebuild -project ios/LearnLens.xcodeproj -scheme LearnLens \
  -destination 'platform=iOS Simulator,name=iPhone 16' build
```

For a real iPhone: pick your team under Signing & Capabilities and change the bundle identifier
(`de.42heilbronn.learnlens`).

If Xcode refuses the project file: create a new iOS App named LearnLens, delete its generated Swift
files, drag in the `LearnLens/` folder, set `LearnLens-Info.plist` as the Info.plist, and add the four
package products above.

## Run without glasses (simulator fallback)

1. Start the backend: `cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000`
2. Run the app in the iOS Simulator. The backend URL defaults to `http://localhost:8000`;
   on a real phone enter `http://<mac-ip>:8000` in the Backend section.
3. Turn on **Simulated glasses (no hardware)**. This pairs a MockDeviceKit Meta Ray-Ban Display and
   renders the same display session in the app. Buttons on the simulated display work.
4. Type a goal such as "Build a sound level meter" (or hold to talk), then **Pick a saved photo and start** or **Start without a photo**.
   The simulated glasses have no camera feed, so glasses capture is disabled in this mode.
   On a real iPhone, **Take a photo with the iPhone and start** is the capture fallback.
5. Answer the part confirmations, confirm each wiring step, approve the sketch, then run
   `python3 agent/learnlens_agent.py --mock --once` on the Mac to complete the (mock) deploy.

## Structure

| File | Role |
|---|---|
| `LearnLensApp.swift` | `Wearables.configure()`, routes Meta AI URL callbacks to `handleUrl` |
| `Services/GlassesManager.swift` | registration, device list, `DeviceSession`, `Display`, simulated glasses |
| `Services/CameraService.swift` | one photo: `addCamera` → stream → `capturePhoto(format: .jpeg)` → `photoDataPublisher` |
| `Services/GlassesSpeechService.swift` | voice input through the glasses microphone (`MWDATSpeech`, experimental in SDK 1.0) |
| `Services/SpeechInputService.swift` | push-to-talk on the iPhone mic (`SFSpeechRecognizer`), used when glasses speech is unavailable; the text field is the last fallback |
| `Services/APIClient.swift` | backend calls; no API keys in the app |
| `ViewModels/SessionViewModel.swift` | UI states and the glasses card for each |
| `Display/GlassesCards.swift` | the card drawn on the glasses (`FlexBox`, `Text`, `ButtonGroup`) |
| `Views/GuideView.swift` | the phone screen |

UI states: Disconnected, Connected, Capturing, Analyzing, Confirm your parts, Guiding, Generating,
Awaiting approval, Deploying, Testing, Success, Error. "Confirm your parts" was added because the
backend holds the wiring plan back until the user has checked the physical parts.

The whole flow can be driven from the glasses: **Speak** (say the goal), **Take photo** (the glasses ask for a
photo of the components), **Yes** for
the part questions, **Done, next** for each wiring step, **Start flash**, and **Yes** after testing.

During wiring every step card has three buttons: **Back** (look at the previous step again, nothing is
undone), **Next** (selected by default) and **Ask**. Ask listens for a question ("Can I use the 5V pin
instead?") while the step stays on screen; the answer then appears with **Back to step**. Answers come from the wiring plan and the
verified hardware facts.

After **Success** the glasses offer **Continue building**. The first thing asked is a photo of the part
you want to add (**Take photo**, or **Skip** for a behaviour change). Then you say what to add or how
the build should behave differently, and **Go**. A new part continues with its questions and wiring
steps; a behaviour change goes directly to new code.

Options on the phone: **Read steps aloud** (on by default) and **Voice commands while wiring**
("next", "back"; experimental, only after glasses speech has worked once in the session).

A follow-up request that cannot be built is answered on the glasses with the reason ("That doesn't
work: …") and **Ideas** that would work instead; picking one continues with it.

A failed step shows **Retry** next to **Start over**. During the test, a `LOUD` line from the board
confirms the build without a button press.

Flashing always needs an explicit approval. It can be given on the glasses ("Start flash" button on
the approval card) or on the phone, where the generated code is shown in full.

The app reads `GET /health` and shows a banner when the backend runs in demo mode (saved AI answers),
when the deploy agent simulates uploads (`--mock`), or when no agent is connected. Demo mode and
simulated uploads are also written on the glasses cards.

## Hardware verification checklist (all unverified)

Apart from the build, nothing below has been run. Tick each one on a real device.

- [x] Project builds with Xcode 27.2 (simulator and device).
- [x] The app launches in the Simulator and reads the backend's `/health`.
- [ ] The app launches on an iPhone.
- [ ] Simulated glasses: toggle on, the display preview shows the LearnLens card, the buttons react.
- [ ] Registration: Developer Mode on in Meta AI, tap Register, return to the app, state is `registered`.
- [ ] Device list shows the glasses with link state `connected`.
- [ ] Connect display: the card appears on the glasses within 10 s.
- [ ] Glasses buttons: "Done, next", "Yes" and "No" reach the app.
- [ ] Camera permission: first capture opens Meta AI, grant, return.
- [ ] Capture with the display session running: a JPEG arrives (camera and display share one session;
      BirdSpotter does this with the standalone photo path, here it is the in-stream path).
- [ ] The card comes back on the glasses after a capture.
- [ ] Glasses card "Speak": microphone permission is granted in Meta AI, the spoken goal appears on the card.
      If glasses speech is unavailable, the iPhone microphone takes over.
- [ ] Glasses card "Photo & start": takes the photo with the glasses camera and starts the flow.
- [ ] Push-to-talk on the phone fills the goal field while the glasses are connected over Bluetooth.
- [ ] Backend reachable from the phone over the LAN (local network permission prompt accepted).
- [ ] iPhone camera fallback opens, and its photo starts the flow (`PhoneCameraPicker.swift` is not covered by the typecheck).
