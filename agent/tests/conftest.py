import os
import sys
from pathlib import Path

os.environ["LEARNLENS_APPROVAL_SECRET"] = "test-approval-secret"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
