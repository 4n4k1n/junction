# LearnLens iOS app

SwiftUI iPhone app for Meta Ray-Ban Display, built on the Meta Wearables Device Access Toolkit 1.0.1
(`MWDATCore`, `MWDATDisplay`, `MWDATCamera`, `MWDATMockDevice`). It follows the official DisplayAccess,
CameraAccess and BirdSpotter samples.

**Status: not yet built with Xcode.** This Mac only has the Command Line Tools, so `xcodebuild` could not
run. What was checked instead:

- Every SDK symbol used was read from the 1.0.1 `.swiftinterface` files in the SDK package.
- `typecheck/typecheck.sh` compiles the sources for macOS against stub modules copied from those
  interfaces. It passes in Swift 5 and Swift 6 mode. It does not cover `MockDisplayPreview.swift`,
  `PhotoEncoding.swift` (UIKit) or the `#if os(iOS)` audio-session lines.
- `LearnLens.xcodeproj` was written by hand (modelled on BirdSpotter's project) and passes `plutil -lint`.
  Opening it in Xcode is untested.

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
4. Type a goal (or hold to talk), then **Pick a saved photo and start** or **Start without a photo**.
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
| `Services/SpeechInputService.swift` | push-to-talk on the iPhone mic (`SFSpeechRecognizer`); the text field is the fallback |
| `Services/APIClient.swift` | backend calls; no API keys in the app |
| `ViewModels/SessionViewModel.swift` | UI states and the glasses card for each |
| `Display/GlassesCards.swift` | the card drawn on the glasses (`FlexBox`, `Text`, `ButtonGroup`) |
| `Views/GuideView.swift` | the phone screen |

UI states: Disconnected, Connected, Capturing, Analyzing, Confirm your parts, Guiding, Generating,
Awaiting approval, Deploying, Testing, Success, Error. "Confirm your parts" was added because the
backend holds the wiring plan back until the user has checked the physical parts.

Approval happens on the phone only, where the code is readable. The glasses show "review on your phone".

The app reads `GET /health` and shows a banner when the backend runs in demo mode (saved AI answers),
when the deploy agent simulates uploads (`--mock`), or when no agent is connected. Demo mode and
simulated uploads are also written on the glasses cards.

## Hardware verification checklist (all unverified)

Nothing below has run on physical glasses or in Xcode. Tick each one on a real device.

- [ ] Project opens and builds in Xcode 26.4+; the app launches in the Simulator.
- [ ] Simulated glasses: toggle on, the display preview shows the LearnLens card, the buttons react.
- [ ] Registration: Developer Mode on in Meta AI, tap Register, return to the app, state is `registered`.
- [ ] Device list shows the glasses with link state `connected`.
- [ ] Connect display: the card appears on the glasses within 10 s.
- [ ] Glasses buttons: "Done, next", "Yes" and "No" reach the app.
- [ ] Camera permission: first capture opens Meta AI, grant, return.
- [ ] Capture with the display session running: a JPEG arrives (camera and display share one session;
      BirdSpotter does this with the standalone photo path, here it is the in-stream path).
- [ ] The card comes back on the glasses after a capture.
- [ ] Push-to-talk fills the goal field while the glasses are connected over Bluetooth.
- [ ] Backend reachable from the phone over the LAN (local network permission prompt accepted).
- [ ] iPhone camera fallback opens, and its photo starts the flow (`PhoneCameraPicker.swift` is not covered by the typecheck).
