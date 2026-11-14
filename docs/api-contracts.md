# API contract

Source of truth: `backend/app/build/models.py`. Machine-readable copy: `shared/schemas/build-api.schema.json`
(generated; a test fails if it is stale). Interactive docs: `http://localhost:8000/docs`.
A complete recorded run: `docs/examples/clap-light-session.json`.

The flow has two halves. A **guided session** takes the user from a photo to finished wiring. It then
hands over a **build**, which carries the sketch through approval, deploy and test.

## 1. Guided session

| Endpoint | Body | Allowed in state | Result |
|---|---|---|---|
| `POST /sessions` | JSON `{"instruction": str, "confirmed_inventory": [InventoryItem]}` | – | `awaiting_analysis` |
| `POST /sessions/{id}/analyze` | multipart: `image` and/or `confirmed_inventory` (JSON list as text) | `awaiting_analysis`, `needs_confirmation` | `needs_confirmation`, `wiring`, `unsupported` or `blocked` |
| `POST /sessions/{id}/analyze` | multipart: `image` | `wiring`, `wiring_complete` | same state, `review` filled |
| `POST /sessions/{id}/confirm-step` | JSON `{"step_index": int, "confirmed": bool, "note": str}` | `wiring` | next step, or `wiring_complete` |
| `POST /sessions/{id}/generate` | optional JSON `{"sensor_active_level": "HIGH"\|"LOW", "debounce_ms": int}` | `wiring_complete`, `code_generated` | `code_generated`; returns `GenerationResult` with `build_id` |
| `GET /sessions/{id}/status` | – | any | – |

Every endpoint returns the full `GuideStatus`, except `generate`, which returns a `GenerationResult`
(see `docs/compile-contract.md`). `generate` writes the sketch with Claude, checks and compiles it, and
never uploads. It can take a minute.

- `analyze` needs an image, a `confirmed_inventory`, or both. Send `confirmed_inventory=[]` to say
  "no photo, ask me about every part".
- `InventoryItem` is `{"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}`. Listing a
  component confirms it is present. `confirmations` are the ids of the `variant` questions the user
  answered yes to.
- `confirm-step` must name the current step (`current_step`), otherwise 409. `confirmed: false`
  keeps the step.
- Errors: 404 unknown session, 409 wrong state or step, 413 image over 5 MB, 415 not JPEG/PNG/WebP/GIF,
  422 unknown component or confirmation id.

### When the wiring plan is released

The plan appears only when all of these hold. Otherwise the state is `needs_confirmation` and
`pending_confirmations` lists the questions.

1. Every required part is either detected with confidence ≥ 0.8 or listed in `confirmed_inventory`.
2. Every check that needs the physical part is confirmed by the user. For the KY-037 that is the pin
   labels. A photo never satisfies these, however confident the model is.
3. The plan passes the electrical checks in `facts.check_safety`. If not, the state is `blocked`.

### GuideStatus

```json
{
  "session_id": "4eb486f6b154",
  "state": "needs_confirmation",
  "instruction": "Build a clap-activated light",
  "display_text": "Please confirm your parts",
  "spoken_text": "I think I see the KY-037 microphone sound sensor, but I am not sure. Is that what you have?",
  "analysis_source": "llm",
  "detected_components": [
    {"component_id": "uno-q", "label": "Arduino board with an LED matrix", "confidence": 0.94,
     "evidence": "'UNO Q' is printed next to the LED matrix; USB-C connector visible."},
    {"component_id": "ky-037", "label": "red sensor board with a microphone capsule", "confidence": 0.62,
     "evidence": "Pin labels are not readable, and the capsule size could also match a KY-038."}
  ],
  "confirmed_inventory": [],
  "pending_confirmations": [
    {"id": "ky-037-present", "component_id": "ky-037", "kind": "presence",
     "question": "I think I see the KY-037 microphone sound sensor, but I am not sure. Is that what you have?",
     "reason": "Detection confidence 62% is below 80%."},
    {"id": "ky037-pin-labels", "component_id": "ky-037", "kind": "variant",
     "question": "Read the 4 pin labels printed on your sensor. Are they AO, G, + and DO?",
     "reason": "Clone modules differ. The wiring plan is only valid for a module with exactly these labels."}
  ],
  "project": {"title": "Clap-activated light", "summary": "A clap toggles the board's built-in LED.",
              "behavior": "toggle_on_event", "board": "uno-q", "input_component": "ky-037",
              "output": "builtin_led", "components": ["uno-q", "ky-037"], "supported": true, "unsupported_reason": ""},
  "hardware_facts": [],
  "wiring_steps": [],
  "current_step": null,
  "confirmed_steps": [],
  "review": null,
  "safety_problems": [],
  "build_id": null
}
```

Who wrote what:

| Field | Comes from |
|---|---|
| `detected_components`, `project.title/summary/behavior` | the vision model (a suggestion; `analysis_source` is `llm`, `mock` or `fallback`) |
| `confirmed_inventory` | the user |
| `hardware_facts` | `hardware/facts/*.json`, each with `source` and `confidence` |
| `wiring_steps`, the sketch | rules over the hardware database (`provenance: rules`) |

In `wiring`, each step looks like:

```json
{"index": 2, "display_text": "KY-037 + to board +3V3",
 "spoken_text": "Connect the sensor pin labelled + to +3V3 on the board.",
 "connection": {"from_pin": {"component": "ky-037", "pin": "+"}, "to_pin": {"component": "uno-q", "pin": "3V3"}},
 "fact_ids": ["ky037-supply", "unoq-3v3-rail"]}
```

### Photo review

```json
"review": {
  "observations": ["Three jumper wires run from the sensor to the board's headers."],
  "possible_issues": ["The wire on the sensor's middle pins is partly hidden; re-check which pin it is on."],
  "electrically_verified": false,
  "disclaimer": "A photo cannot verify an electrical connection. Check each wire against the steps by hand before you plug in USB."
}
```

`electrically_verified` is typed as the constant `false`. A review never confirms a step.

## 2. Build: approval, deploy, test

`generate` returns a `build_id`. That build starts in `awaiting_approval` with the sketch filled.

| Endpoint | Body | Allowed in state | Result state |
|---|---|---|---|
| `GET /builds/{id}` | – | any | – |
| `POST /builds/{id}/approve` | `{"sketch_sha256": str}` | `awaiting_approval`, or `failed` after a failed deploy (retry) | `approved` |
| `POST /builds/{id}/reject` | – | `awaiting_approval` | `rejected` |
| `POST /builds/{id}/test-result` | `{"passed": bool, "note": str}` | `awaiting_test` | `done` / `failed` |

While `approved` or `deploying`, the app polls `GET /builds/{id}` (1 s). `deploy_events` grows as the
agent works (`{stage, status, message, ts}`), and `deploy` holds the final result.
Errors: 404 unknown build, 409 wrong state or hash mismatch.

`status` ∈ `wiring | awaiting_approval | approved | deploying | awaiting_test | done | failed | rejected`.

`POST /builds`, `POST /builds/photo` and `POST /builds/{id}/wiring/confirm` still exist from the first
scaffold. They skip part confirmation, so clients should use sessions instead.

### Deploy agent

| Endpoint | Body | Returns |
|---|---|---|
| `POST /agent/jobs/claim` | `{"agent_id": str}` | 200 `DeployJob`, or 204 when nothing is approved |
| `POST /agent/jobs/{job_id}/events` | `DeployEvent` | `Build` |
| `POST /agent/jobs/{job_id}/result` | `DeployResult` | `Build` |

Header `X-Agent-Token` is required when the backend has `LEARNLENS_AGENT_TOKEN` set (else 401).
Payloads, the approval token and the agent's own local API are in `docs/compile-contract.md` and
`agent/README.md`.

## Tutor API

The earlier Socratic tutor moved from `/sessions` to `/tutor/sessions` so the guided session could
take `/sessions`. It is documented in the root `README.md`.
