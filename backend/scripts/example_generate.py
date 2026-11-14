"""Generates the clap-light sketch for a confirmed wiring plan and compiles it with the real
UNO Q core if arduino-cli and arduino:zephyr are installed. No API key, no board, no upload.

    cd backend && .venv/bin/python scripts/example_generate.py            # template sketch
    cd backend && .venv/bin/python scripts/example_generate.py --repair   # canned AI answers: one
                                                                          # with a compile error, then a fix

Writes docs/examples/clap-light-generated.ino and clap-light-generation.json (template run only).
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("LEARNLENS_TUTOR", "mock")
os.environ.setdefault("LEARNLENS_PROJECTS_DIR", str(Path(tempfile.mkdtemp()) / "generated"))

import example_clap_session as example  # noqa: E402

OUT = ROOT / "docs" / "examples"
INVENTORY = [{"component_id": "uno-q"}, {"component_id": "ky-037", "confirmations": ["ky037-pin-labels"]}]

if __name__ == "__main__":
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    repair = "--repair" in sys.argv
    if repair:
        # Passes the static checks but does not compile: the compiler log drives the repair.
        typo = dict(example.CANNED_SKETCH, source_code=example.CANNED_SKETCH["source_code"].replace(
            'Monitor.println("SOUND_DETECTED");', 'Montior.println("SOUND_DETECTED");'))
        example.use_fake_codegen(typo, example.CANNED_SKETCH)

    sid = client.post("/sessions", json={"instruction": example.INSTRUCTION, "confirmed_inventory": INVENTORY}).json()["session_id"]
    s = client.post(f"/sessions/{sid}/analyze", data={"confirmed_inventory": "[]"}).json()
    for i in range(len(s["wiring_steps"])):
        client.post(f"/sessions/{sid}/confirm-step", json={"step_index": i})
    r = client.post(f"/sessions/{sid}/generate")
    assert r.status_code == 200, r.text
    g = r.json()

    for a in g["attempts"]:
        compiled = a["compile"]["status"] if a["compile"] else "not compiled"
        print(f"attempt {a['attempt']} ({a['kind']}): {len(a['issues'])} issue(s), compile: {compiled}")
        for issue in a["issues"]:
            print(f"    [{issue['check']}/{issue['rule']}] {issue['message'].splitlines()[0][:150]}")
    print(f"source={g['source']} repairs_used={g['repairs_used']} compile={g['compile']['status']} "
          f"{g['compile']['detail']} flashed={g['flashed']} hardware_verified={g['hardware_verified']}")
    print("\n".join(l for l in g["compile"]["log"].splitlines() if l.startswith(("Sketch uses", "Global variables"))))

    if not repair:
        g["project_dir"] = "backend/generated/<session_id>/attempt-0/learnlens_sketch"
        for a in g["attempts"]:
            a["project_dir"] = g["project_dir"]
        for report in [g["compile"]] + [a["compile"] for a in g["attempts"] if a["compile"]]:
            report["command"] = ["arduino-cli"] + report["command"][1:-1] + [g["project_dir"]] if report["command"] else []
            report["log"] = "\n".join(l for l in report["log"].splitlines() if l.startswith(("Sketch uses", "Global variables")))
        (OUT / "clap-light-generated.ino").write_text(g["sketch"]["code"])
        (OUT / "clap-light-generation.json").write_text(json.dumps(g, indent=2) + "\n")
        print(f"wrote {OUT}/clap-light-generated.ino and clap-light-generation.json")
