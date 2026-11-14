"""Live check of the vision step against Claude (costs one API call, two with --review).

    cd backend && ANTHROPIC_API_KEY=... .venv/bin/python scripts/live_analyze.py desk.jpg "Build a clap-activated light"

Prints what the model detected and the project it proposes. No mock fallback: errors surface.
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.build import planner  # noqa: E402
from app.build.analyzer import EFFORT, MODEL, ClaudeAnalyzer, to_spec  # noqa: E402

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--review"]
    if not args:
        sys.exit(__doc__)
    image = Path(args[0]).read_bytes()
    media_type = "image/png" if args[0].lower().endswith(".png") else "image/jpeg"
    claude = ClaudeAnalyzer()
    print(f"model={MODEL} effort={EFFORT}")
    t0 = time.perf_counter()
    if "--review" in sys.argv:
        result = claude.review(image, media_type, planner.plan_wiring(planner.clap_light_spec()))
    else:
        result = claude.analyze(args[1] if len(args) > 1 else "Build a clap-activated light", image, media_type)
        print("spec:", to_spec(result.project).model_dump_json(indent=1))
    print(json.dumps(result.model_dump(), indent=1))
    print(f"{time.perf_counter() - t0:.1f} s")
