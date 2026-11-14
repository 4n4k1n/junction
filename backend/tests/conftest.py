import os
import sys
import tempfile
from pathlib import Path

# Must be set before app modules are imported.
os.environ["LEARNLENS_TUTOR"] = "mock"
os.environ["LEARNLENS_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["LEARNLENS_ARDUINO_CLI"] = "none"  # unit tests never start a real compile
os.environ["LEARNLENS_PROJECTS_DIR"] = os.path.join(tempfile.mkdtemp(), "generated")
os.environ["LEARNLENS_APPROVAL_SECRET"] = "test-approval-secret"
os.environ["LEARNLENS_PREGENERATE"] = "0"   # tests decide when generation runs; test_optimizations covers it
os.environ["LEARNLENS_STATE_FILE"] = "none"  # no state file in unit tests
# The clap light is no longer offered to users, but its code path stays covered by these tests.
os.environ["LEARNLENS_OFFERED_PROJECTS"] = "level_meter,toggle_on_event,on_while_active"
