"""Smoke test of the deploy agent against the real toolchain and, with --flash, a real board.

    python3 agent/smoke_test.py            # toolchain + board discovery + real compile. Uploads nothing.
    python3 agent/smoke_test.py --flash    # also uploads the sample sketch, after you type "flash"

Runs the agent in-process (no backend, no HTTP). With --flash, typing "flash" is the explicit
approval: the script then signs an approval token with the local key, exactly as the backend does
when the user approves in the app. Prints what the tools actually reported, nothing more.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lldeploy import approval  # noqa: E402
from lldeploy.core import DeployAgent, Rejected  # noqa: E402

SAMPLE = Path(__file__).resolve().parents[1] / "docs" / "examples" / "clap-light-generated.ino"
EXPECTED = ["READY", "SOUND_DETECTED", "LED_ON", "LED_OFF"]
BUILD_ID = "0c0ffee0"


def sign(build_id: str, sha256: str, ttl_s: int = 120) -> str:
    payload = {"build_id": build_id, "sha256": sha256, "exp": int(time.time()) + ttl_s, "nonce": secrets.token_hex(8)}
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    return body + "." + hmac.new(approval.load_key(), body.encode(), hashlib.sha256).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--flash", action="store_true", help="upload the sample sketch to the connected UNO Q")
    ap.add_argument("--listen", type=float, default=20, help="seconds to read board output after the upload")
    args = ap.parse_args()

    code = SAMPLE.read_text()
    sha = hashlib.sha256(code.encode()).hexdigest()
    agent = DeployAgent(on_event=lambda _, e: print(f"  [{e['stage']}] {e['status']}: {e['message']}"))
    agent.config.log_capture_s = args.listen
    try:
        device = agent.device()
        print("1. device")
        print(f"  arduino-cli: {device['toolchain']['arduino_cli']}")
        print(f"  core arduino:zephyr: {device['toolchain']['core']}")
        print(f"  boards: {device['boards'] or 'none'}   {device['problem']}")
        if not device["toolchain"]["ready"]:
            return 1

        print(f"2. compile {SAMPLE.name} for {agent.config.fqbn}")
        compiled = agent.compile({"build_id": BUILD_ID, "sketch": {"filename": "learnlens_sketch.ino", "code": code, "sha256": sha}})
        for d in compiled["diagnostics"]:
            print(f"  {d['severity']} {d['file']}:{d['line']}: {d['message']}")
        print("  " + "\n  ".join(l for l in compiled["log"].splitlines() if l.startswith(("Sketch uses", "Global variables"))))
        if compiled["status"] != "ok":
            print(compiled["log"][-1500:])
            return 1

        if not args.flash:
            print("3. upload skipped (run with --flash to upload to a connected board)")
            return 0
        if not device["connected"]:
            print("3. no Arduino UNO Q found: nothing uploaded")
            return 1
        if input(f"3. Upload to {device['boards'][0]['port']}? Type 'flash' to approve: ").strip() != "flash":
            print("  not approved: nothing uploaded")
            return 1
        result = agent.flash(BUILD_ID, sign(BUILD_ID, sha), EXPECTED)
        print("  upload tool output:\n    " + "\n    ".join(result["log"].splitlines()[-12:]))
        print(f"4. board output during {args.listen:.0f} s (clap now):")
        print("    " + ("\n    ".join(result["runtime_log"]) or "(nothing received)"))
        print(f"  expected events seen: {result['observed_events']} of {EXPECTED}")
        return 0
    except Rejected as e:
        print(f"STOPPED at {e.stage}: {e}")
        return 1
    finally:
        agent.close()


if __name__ == "__main__":
    sys.exit(main())
