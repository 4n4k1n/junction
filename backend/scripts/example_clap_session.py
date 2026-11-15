"""Runs the clap-activated light through the whole API with canned AI responses (no API key,
no hardware) and writes every request/response pair to docs/examples/clap-light-session.json.

    cd backend && .venv/bin/python scripts/example_clap_session.py
"""
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "agent"))
os.environ.setdefault("LEARNLENS_TUTOR", "mock")
os.environ.setdefault("LEARNLENS_STATE_FILE", "none")  # examples leave no state behind

OUT = ROOT / "docs" / "examples" / "clap-light-session.json"
INSTRUCTION = "Build a clap-activated light"
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 100

sys.path.insert(0, str(ROOT / "backend"))

# This recording exercises the clap light, which is no longer offered to users by default.
os.environ.setdefault("LEARNLENS_OFFERED_PROJECTS", "level_meter,toggle_on_event,on_while_active")
# An answer in the shape the vision model returns: sure about the board, unsure about the sensor.
CANNED_ANALYSIS = dict(components=[
    {
        "component_id": "uno-q",
        "label": "Arduino board with an LED matrix",
        "confidence": 0.94,
        "evidence": "'UNO Q' is printed next to the LED matrix; USB-C connector visible."
    },
    {
        "component_id": "ky-037",
        "label": "red sensor board with a microphone capsule",
        "confidence": 0.62,
        "evidence": "Microphone capsule, blue trim pot and 4 pins visible. Pin labels are not readable, and the capsule size could also match a KY-038."
    },
    {
        "component_id": "unknown",
        "label": "bundle of jumper wires",
        "confidence": 0.9,
        "evidence": "Loose wires."
    }
], project={
    "title": "Clap-activated light", "summary": "A clap toggles the board's built-in LED.",
    "behavior": "toggle_on_event", "input_component": "ky-037", "unsupported_reason": ""})

CANNED_REVIEW = {
    "observations": ["Three jumper wires run from the sensor to the board's headers.",
                     "The sensor's power LED appears to be lit."],
    "possible_issues": ["The wire on the sensor's middle pins is partly hidden; re-check which pin it is on."],
}


# A sketch as the model is asked to return it. Hand-written for tests and this example: it is NOT
# the output of a real model call.
CANNED_SKETCH = {
    "source_code": """\
// Clap-activated light for the Arduino UNO Q.
#include <Arduino_RouterBridge.h>

const int SENSOR_PIN = 2;               // KY-037 DO on header D2
const int LED_PIN = LED_BUILTIN;        // red LED3, lights at LOW
const int SENSOR_ACTIVE_LEVEL = HIGH;   // DO level while the sensor hears sound
const unsigned long DEBOUNCE_MS = 300;  // one clap counts once

bool ledIsOn = false;
int lastLevel = LOW;
unsigned long lastClapMs = 0;

void setup() {
  pinMode(SENSOR_PIN, INPUT);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);  // off
  Monitor.begin();
  Monitor.println("READY");
}

void loop() {
  int level = digitalRead(SENSOR_PIN);
  bool justBecameActive = level == SENSOR_ACTIVE_LEVEL && lastLevel != SENSOR_ACTIVE_LEVEL;
  if (justBecameActive && millis() - lastClapMs >= DEBOUNCE_MS) {
    lastClapMs = millis();
    Monitor.println("SOUND_DETECTED");
    ledIsOn = !ledIsOn;
    digitalWrite(LED_PIN, ledIsOn ? LOW : HIGH);
    Monitor.println(ledIsOn ? "LED_ON" : "LED_OFF");
  }
  lastLevel = level;
}
""",
    "explanation": "The sketch watches the sound sensor on pin D2. When the signal switches to its active level, "
                   "it flips the built-in LED and prints what happened. After each clap it ignores the sensor for "
                   "300 ms so that one clap is not counted twice.",
    "assumptions": ["The sensor output is HIGH while it hears sound.",
                    "The built-in LED lights when its pin is LOW.",
                    "The trim potentiometer is set so the sensor is quiet in a silent room."],
    "expected_serial_events": [
        {"event": "READY", "when": "once after start-up"},
        {"event": "SOUND_DETECTED", "when": "each accepted clap"},
        {"event": "LED_ON", "when": "after the LED was switched on"},
        {"event": "LED_OFF", "when": "after the LED was switched off"},
    ],
}


class FakeAnthropic:
    """Stands in for anthropic.Anthropic: returns queued JSON documents and records each request."""

    def __init__(self, *responses):
        self.queue = list(responses)
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        text = item if isinstance(item, str) else json.dumps(item)
        return SimpleNamespace(stop_reason="end_turn", stop_details=None,
                               content=[SimpleNamespace(type="text", text=text)])


def use_fake_ai(*responses) -> FakeAnthropic:
    from app.build import session_router
    from app.build.analyzer import Analyzer, ClaudeAnalyzer

    fake = FakeAnthropic(*responses)
    session_router.analyzer = Analyzer(mode="claude", claude=ClaudeAnalyzer(client=fake))
    return fake


def use_fake_codegen(*responses, compiler=None) -> FakeAnthropic:
    from app.build import session_router
    from app.build.codegen import CodeGenerator

    fake = FakeAnthropic(*responses)
    session_router.generator = CodeGenerator(mode="claude", client=fake, compiler=compiler)
    return fake


def run() -> list:
    from fastapi.testclient import TestClient

    import learnlens_agent
    from app.main import app

    client = TestClient(app)
    use_fake_ai(CANNED_ANALYSIS, CANNED_REVIEW)
    use_fake_codegen(CANNED_SKETCH)
    log = []

    def call(title, method, path, **kwargs):
        r = client.request(method, path, **kwargs)
        assert r.status_code == 200, (path, r.status_code, r.text)
        request = {k: v for k, v in kwargs.items() if k in ("json", "data")}
        if "files" in kwargs:
            request["files"] = {"image": "<desk photo, image/jpeg>"}
        log.append({"step": title, "request": {"method": method, "path": path, **request}, "response": r.json()})
        return r.json()

    photo = lambda: {"image": ("desk.jpg", io.BytesIO(FAKE_JPEG), "image/jpeg")}  # noqa: E731

    s = call("1. Start a session", "POST", "/sessions", json={"instruction": INSTRUCTION})
    sid = s["session_id"]
    s = call("2. Analyze the desk photo: the sensor is uncertain, so the plan is held back",
             "POST", f"/sessions/{sid}/analyze", files=photo())
    assert s["state"] == "needs_confirmation" and not s["wiring_steps"]
    inventory = [{"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]
    s = call("3. The user confirms the sensor and its pin labels: wiring plan released",
             "POST", f"/sessions/{sid}/analyze", data={"confirmed_inventory": json.dumps(inventory)})
    assert s["state"] == "wiring"
    for i in range(len(s["wiring_steps"])):
        if i == 2:
            call("   Optional photo review mid-wiring (never an electrical verification)",
                 "POST", f"/sessions/{sid}/analyze", files=photo())
        s = call(f"4.{i} Confirm wiring step {i}", "POST", f"/sessions/{sid}/confirm-step",
                 json={"step_index": i, "confirmed": True})
    assert s["state"] == "wiring_complete"
    g = call("5. Generate the sketch (here: canned AI answer; static checks; compile if arduino-cli is set up)",
             "POST", f"/sessions/{sid}/generate", json={"debounce_ms": 300})
    s = call("   Status", "GET", f"/sessions/{sid}/status")

    b = call("6. The generated sketch, waiting for approval", "GET", f"/builds/{g['build_id']}")
    call("7. Explicit approval of that exact sketch", "POST", f"/builds/{b['build_id']}/approve",
         json={"sketch_sha256": b["sketch"]["sha256"]})

    def agent_post(path, payload):
        r = client.post(path, json=payload)
        return r.status_code, (r.json() if r.content else None)

    # The mock deploy agent takes approved jobs in order; run it until ours has been handled.
    for _ in range(20):
        if client.get(f"/builds/{b['build_id']}").json()["status"] != "approved":
            break
        learnlens_agent.run_once(agent_post, learnlens_agent.mock_agent())
    call("8. After the (mock) deploy agent ran", "GET", f"/builds/{b['build_id']}")
    call("9. The user claps and reports the result", "POST", f"/builds/{b['build_id']}/test-result",
         json={"passed": True})
    call("10. Health: what the backend is running with", "GET", "/health")
    return log


if __name__ == "__main__":
    log = run()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(log, indent=2) + "\n")
    for entry in log:
        r = entry["response"]
        print(f"{entry['step']}\n    -> {r.get('state') or r.get('status')}: {r.get('display_text', '')}")
    print(f"\nwrote {OUT}")
