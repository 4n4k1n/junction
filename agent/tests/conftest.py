import os
import sys
from pathlib import Path

os.environ["LEARNLENS_APPROVAL_SECRET"] = "test-approval-secret"
os.environ.setdefault("LEARNLENS_TUTOR", "mock")       # no AI calls from agent tests
os.environ.setdefault("LEARNLENS_STATE_FILE", "none")  # no state file from agent tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
