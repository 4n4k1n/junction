"""Write the build API's JSON Schemas to shared/schemas/ for the iOS app and the deploy agent.

    cd backend && .venv/bin/python scripts/export_schemas.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic.json_schema import models_json_schema  # noqa: E402

from app.build.models import API_MODELS  # noqa: E402

OUT = Path(__file__).resolve().parents[2] / "shared" / "schemas" / "build-api.schema.json"


def render() -> str:
    _, schema = models_json_schema([(m, "serialization") for m in API_MODELS], title="LearnLens build API")
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print(f"wrote {OUT}")
