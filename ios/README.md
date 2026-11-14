# iOS Companion App (Person 1)

Not scaffolded on purpose: start from the official **CameraAccess** and **DisplayAccess** samples in
`meta/meta-wearables-dat-ios` instead of a fresh SDK project, and copy them into this folder.

## What the app has to do

1. `POST /sessions` on launch → show `display_text` on the glasses (MWDATDisplay) and on the phone.
2. Capture button / voice trigger → grab a photo from the glasses camera (fallback: iPhone camera),
   downscale to ~1280 px JPEG (< 5 MB), then:

   ```
   POST /sessions/{id}/interact   (multipart/form-data)
     interaction_type = photo | question | answer | hint
     user_message     = transcribed speech (SFSpeechRecognizer on the phone is fine)
     image            = photo.jpg   (optional)
   ```
3. Render the response: `display_text` (≤ 12 words) big and high-contrast, `step.step_index+1 / total_steps`,
   `Hint {hint_level}/{max_hints}`. Speak `spoken_text` with AVSpeechSynthesizer.
4. When `action == "ask_question"`, the next voice input is sent with `interaction_type=answer`.
5. "It lights up" button → `POST /sessions/{id}/confirm`.

## Fallbacks (keep working if hardware fails)
- Glasses camera fails → iPhone camera, same endpoint.
- Glasses display fails → same view in the phone app (mirror to the projector).
- Backend down → the browser console at `/` on a laptop with `LEARNLENS_TUTOR=mock`.
