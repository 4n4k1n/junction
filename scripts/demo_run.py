#!/usr/bin/env python3
"""Runs the whole LearnLens flow from the terminal, against a running backend and deploy agent.
It does what the phone app does, so the flow can be rehearsed and timed without the glasses.

    ./scripts/start.sh                   # in one terminal
    python3 scripts/demo_run.py          # in another

    python3 scripts/demo_run.py --photo desk.jpg          # send a photo of the parts as an extra check
    python3 scripts/demo_run.py --auto                    # answer every prompt except the upload approval

The upload approval is always typed by a person. Standard library only.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid

GOAL = "Build a Pong game."


class Api:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None, form=None, timeout=60):
        headers, data = {}, None
        if body is not None:
            headers["Content-Type"], data = "application/json", json.dumps(body).encode()
        elif form is not None:
            boundary = uuid.uuid4().hex
            headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
            parts = []
            for name, value in form.items():
                if isinstance(value, bytes):
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="photo.jpg"\r\n'
                                 f"Content-Type: image/jpeg\r\n\r\n".encode() + value + b"\r\n")
                else:
                    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
            data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            sys.exit(f"\nSTOPPED: {method} {path} -> {e.code} {e.read().decode()[:400]}")
        except (urllib.error.URLError, OSError) as e:
            sys.exit(f"\nSTOPPED: backend not reachable at {self.base} ({e}). Start it with ./scripts/start.sh")


def glasses(text):
    print(f"   [glasses] {text}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", default="http://localhost:8000")
    ap.add_argument("--photo", help="JPEG of the parts on the desk")
    ap.add_argument("--auto", action="store_true", help="answer part and wiring prompts automatically")
    ap.add_argument("--goal", default=GOAL)
    args = ap.parse_args()
    api, t0 = Api(args.backend), time.time()

    def ask(prompt):
        if args.auto:
            print(f"   {prompt} [auto: yes]")
            return ""
        return input(f"   {prompt} ").strip().lower()

    health = api.call("GET", "/health", timeout=5)
    print("== LearnLens run ==")
    print(f"   AI: {'Claude' if health['ai_available'] else 'none (prevalidated sketches only)'}")
    if not health["agent_connected"]:
        sys.exit("STOPPED: no deploy agent is connected to the backend. Start it with ./scripts/start.sh")
    print(f"   Board: {'SIMULATED upload (agent --mock)' if health['agent_mock'] else 'real upload'}\n")

    print(f'1. Goal: "{args.goal}"')
    s = api.call("POST", "/sessions", body={"instruction": args.goal})
    sid = s["session_id"]

    print("2. Photo of the parts")
    form = {"image": open(args.photo, "rb").read()} if args.photo else {"confirmed_inventory": "[]"}
    s = api.call("POST", f"/sessions/{sid}/analyze", form=form, timeout=90)
    print(f"   analysis source: {s['analysis_source']}")
    for part in s["detected_components"]:
        print(f"   saw: {part['label']} -> {part['component_id']} ({part['confidence']:.0%})")

    print("3. Confirm the parts")
    while s["state"] == "needs_confirmation":
        pending = s["pending_confirmations"][0]
        glasses(pending["question"])
        if ask("Yes? [Enter = yes, n = no]") == "n":
            sys.exit("STOPPED: part not confirmed.")
        item = {"component_id": pending["component_id"],
                "confirmations": [pending["id"]] if pending["kind"] == "variant" else []}
        s = api.call("POST", f"/sessions/{sid}/analyze", form={"confirmed_inventory": json.dumps([item])})
    if s["state"] != "wiring":
        sys.exit(f"STOPPED: session is {s['state']}: {s['spoken_text']} {s['safety_problems']}")

    print("4. Wiring")
    while s["state"] == "wiring":
        i = s["current_step"]
        glasses(f"Step {i + 1} of {len(s['wiring_steps'])}: {s['wiring_steps'][i]['display_text']}")
        ask("Done? [Enter]")
        s = api.call("POST", f"/sessions/{sid}/confirm-step", body={"step_index": i})

    print("5. Generate and compile the sketch")
    glasses("Writing the sketch…")
    g = api.call("POST", f"/sessions/{sid}/generate", timeout=300)
    print(f"   source: {g['source']}   repairs: {g['repairs_used']}   compile: {g['compile']['status']} {g['compile']['detail']}")
    for line in g["compile"]["log"].splitlines():
        if line.startswith("Sketch uses"):
            print(f"   {line}")
    bid = g["build_id"]

    print("6. Approval")
    glasses("Review and approve the code on your phone.")
    print(f"   {g['explanation']}")
    if input("   Type 'approve' to upload this sketch to the board: ").strip() != "approve":
        sys.exit("STOPPED: not approved. Nothing was uploaded.")
    api.call("POST", f"/builds/{bid}/approve", body={"sketch_sha256": g["sketch"]["sha256"]})

    print("7. Upload")
    shown, build = 0, None
    for _ in range(240):
        build = api.call("GET", f"/builds/{bid}")
        for event in build["deploy_events"][shown:]:
            print(f"   [{event['stage']}] {event['status']}: {event['message']}")
        shown = len(build["deploy_events"])
        if build["status"] not in ("approved", "deploying"):
            break
        time.sleep(1)
    if build["status"] != "awaiting_test":
        sys.exit(f"STOPPED: deploy {build['status']}: {build['error']}\n{(build['deploy'] or {}).get('log', '')[-800:]}")
    simulated = build["deploy"]["mock"]
    print("   SIMULATED: no board was flashed." if simulated else f"   uploaded to {build['deploy']['port']} (the upload tool reported success)")

    print("8. Test")
    glasses("Make some noise. Does it react?")
    if not simulated:
        seen, deadline = 0, time.time() + 20
        while time.time() < deadline:
            deploy = api.call("GET", f"/builds/{bid}")["deploy"]
            for line in deploy["runtime_log"][seen:]:
                print(f"   [board] {line}")
            seen = len(deploy["runtime_log"])
            if "SOUND_DETECTED" in deploy["observed_events"] and any(e in deploy["observed_events"] for e in ("LED_ON", "LED_OFF")):
                print("   the board reported the clap and the LED change")
                break
            time.sleep(1)
        else:
            print("   no sound event was reported in the board log within 20 s (the LEDs are the ground truth)")
    answer = "y" if simulated and args.auto else input("   Did the board react to sound as described? [y/n] ").strip().lower()
    build = api.call("POST", f"/builds/{bid}/test-result", body={"passed": answer == "y"})

    label = "SIMULATED upload" if simulated else "real board"
    if build["status"] == "done":
        glasses(f"Success{' · SIMULATED upload' if simulated else ''}")
        print(f"\nRESULT: success ({label}{'' if health['ai_available'] else ', no AI'}) in {time.time() - t0:.0f} s")
        return 0
    print(f"\nRESULT: failed ({label})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
