"""Deploy agent with mocked subprocess calls and hardware transport: no arduino-cli, no board."""
import hashlib
import json
import subprocess
import threading
import urllib.error
import urllib.request

import pytest

from app.build import approval as backend_approval  # the backend side of the token
from lldeploy import core, server, tools

CLI = "/fake/bin/arduino-cli"
ADB = "/fake/bin/adb"
PORT = "/dev/cu.usbmodem14101"
CODE = """#include <Arduino_RouterBridge.h>
void setup() { Monitor.begin(); Monitor.println("READY"); }
void loop() {}
"""
BOARD_JSON = json.dumps({"detected_ports": [
    {"port": {"address": "/dev/cu.Bluetooth-Incoming-Port", "protocol": "serial", "properties": {}}},
    {"port": {"address": PORT, "protocol": "serial",
              "properties": {"vid": "0x2341", "pid": "0x0078", "serialNumber": "2E4A9F0113"}},
     "matching_boards": [{"name": "Arduino UNO Q", "fqbn": "arduino:zephyr:unoq"}]},
]})
NO_BOARD_JSON = json.dumps({"detected_ports": []})
CORE_JSON = json.dumps({"platforms": [{"id": "arduino:zephyr", "installed_version": "1.1.0"}]})
COMPILE_ERROR = ("/WS/abc123/learnlens_sketch/learnlens_sketch.ino:2:16: error: 'Montior' was not declared in this scope\n"
                 "/WS/abc123/learnlens_sketch/learnlens_sketch.ino:3:1: warning: unused variable 'x'\n"
                 "Error during build: exit status 1\n")


def artifact(code=CODE, build_id="abc123", **sketch):
    s = {"filename": "learnlens_sketch.ino", "code": code, "sha256": hashlib.sha256(code.encode()).hexdigest()}
    s.update(sketch)
    return {"build_id": build_id, "sketch": s, "fqbn": "arduino:zephyr:unoq"}


def token(code=CODE, build_id="abc123", ttl=300):
    return backend_approval.mint(build_id, hashlib.sha256(code.encode()).hexdigest(), ttl)


class FakeTools:
    """Stands in for subprocess: scripted results per arduino-cli subcommand, and a record of every call."""

    def __init__(self, monkeypatch, board=BOARD_JSON, compile=(0, "Sketch uses 87032 bytes"),
                 upload=(0, "Upload completed"), monitor=("READY", "SOUND_DETECTED", "LED_ON")):
        self.calls, self.results, self.monitor = [], {"board": (0, board), "core": (0, CORE_JSON),
                                                      "compile": compile, "upload": upload}, monitor
        self.adb_states = ["device"]
        monkeypatch.setattr(subprocess, "run", self.run)
        monkeypatch.setattr(subprocess, "Popen", self.popen)

    def run(self, argv, **kwargs):
        assert kwargs.get("shell") is False and isinstance(argv, list)
        self.calls.append(argv)
        if argv[0] == ADB:  # adb get-state / reconnect: scripted board states, the last one repeats
            if argv[-1] != "get-state":
                return subprocess.CompletedProcess(argv, 0, stdout="reconnecting", stderr="")
            state = self.adb_states.pop(0) if len(self.adb_states) > 1 else self.adb_states[0]
            ok = state == "device"
            return subprocess.CompletedProcess(argv, 0 if ok else 1, stdout="device\n" if ok else "",
                                               stderr="" if ok else f"error: device {state}")
        outcome = self.results[argv[1]]
        if isinstance(outcome, list):  # a list of outcomes: one per call, the last one repeats
            outcome = outcome.pop(0) if len(outcome) > 1 else outcome[0]
        code, out = outcome
        if isinstance(code, Exception):
            raise code
        return subprocess.CompletedProcess(argv, code, stdout=out, stderr="")

    def popen(self, argv, **kwargs):
        assert kwargs.get("shell") is False and isinstance(argv, list)
        self.calls.append(argv)
        lines = self.monitor

        class Proc:
            stdout = iter(line + "\n" for line in lines)

            def poll(self):
                return 0

            def terminate(self):
                pass

            def wait(self, timeout=None):
                return 0

        return Proc()

    def commands(self):
        return [c[1] for c in self.calls]


@pytest.fixture
def agent():
    a = core.DeployAgent(tools.Config(cli=CLI, adb=None, log_capture_s=0.2))
    yield a
    a.close()


def reject_stage(fn):
    with pytest.raises(core.Rejected) as e:
        fn()
    return e.value.stage, str(e.value)


# ---------- device ----------

def test_device_found_by_usb_id(agent, monkeypatch):
    FakeTools(monkeypatch)
    d = agent.device()
    assert d["connected"] and d["toolchain"] == {"arduino_cli": CLI, "core": "1.1.0", "ready": True}
    assert d["boards"] == [{"port": PORT, "protocol": "serial", "serial_number": "2E4A9F0113",
                            "fqbn": "arduino:zephyr:unoq", "adb_state": "unknown"}]   # this agent has no adb configured


def test_missing_device_and_missing_toolchain(agent, monkeypatch):
    FakeTools(monkeypatch, board=NO_BOARD_JSON)
    d = agent.device()
    assert not d["connected"] and d["problem"] == "no Arduino UNO Q found on USB"

    agent.compile(artifact())
    stage, message = reject_stage(lambda: agent.flash("abc123", token()))
    assert stage == "device" and "Plug it in" in message

    bare = core.DeployAgent(tools.Config(cli=None, adb=None))
    assert "arduino-cli not found" in bare.device()["problem"]
    assert reject_stage(lambda: bare.compile(artifact()))[0] == "compile"
    bare.close()


# ---------- compile ----------

def test_compile_uses_one_fixed_command_inside_the_workspace(agent, monkeypatch):
    fake = FakeTools(monkeypatch)
    r = agent.compile(artifact())
    assert r["status"] == "ok" and r["diagnostics"] == []
    ws = str(agent.workspace)
    assert fake.calls == [[CLI, "compile", "--fqbn", "arduino:zephyr:unoq", "--warnings", "default", "--no-color",
                           "--build-path", f"{ws}/abc123/build", f"{ws}/abc123/learnlens_sketch"]]
    assert (agent.workspace / "abc123/learnlens_sketch/learnlens_sketch.ino").read_text() == CODE


def test_compile_failure_returns_diagnostics_and_blocks_flash(agent, monkeypatch):
    fake = FakeTools(monkeypatch, compile=(1, COMPILE_ERROR))
    r = agent.compile(artifact())
    assert r["status"] == "failed"
    assert r["diagnostics"] == [
        {"file": "learnlens_sketch.ino", "line": 2, "column": 16, "severity": "error",
         "message": "'Montior' was not declared in this scope"},
        {"file": "learnlens_sketch.ino", "line": 3, "column": 1, "severity": "warning", "message": "unused variable 'x'"}]
    assert reject_stage(lambda: agent.flash("abc123", token()))[0] == "compile"
    assert "upload" not in fake.commands()


@pytest.mark.parametrize("bad,reason", [
    (artifact(sha256="0" * 64), "sha256"),
    (artifact(filename="../../evil.ino"), "filename"),
    (artifact(filename="sketch.sh"), "filename"),
    (artifact(build_id="../../etc"), "build_id"),
    (artifact(code=""), "code"),
    (artifact(code="x" * 20001), "code"),
    (artifact(code='#include "/etc/passwd"\nvoid setup(){}\nvoid loop(){}\n'), "preprocessor"),
    (artifact(code="#include <Servo.h>\nvoid setup(){}\nvoid loop(){}\n"), "preprocessor"),
    (artifact(code='/* x */ #include "/Users/me/.ssh/id_rsa"\nvoid setup(){}\nvoid loop(){}\n'), "preprocessor"),
    (artifact(code="#define X 1\nvoid setup(){}\nvoid loop(){}\n"), "preprocessor"),
    (artifact(code='%:include "/etc/passwd"\nvoid setup(){}\nvoid loop(){}\n'), "digraph"),
    (dict(artifact(), fqbn="arduino:avr:uno"), "only builds for arduino:zephyr:unoq"),
    ({"build_id": "abc123"}, "artifact must be"),
    ("rm -rf /", "artifact must be"),
])
def test_invalid_code_or_artifact_is_rejected_before_any_tool_runs(agent, monkeypatch, bad, reason):
    fake = FakeTools(monkeypatch)
    stage, message = reject_stage(lambda: agent.compile(bad))
    assert stage == "validate" and reason in message
    assert fake.calls == [] and list(agent.workspace.iterdir()) == []


# ---------- approval ----------

def test_missing_or_wrong_approval_never_uploads(agent, monkeypatch):
    fake = FakeTools(monkeypatch)
    agent.compile(artifact())
    good = token()
    body, _, sig = good.partition(".")
    for bad, reason in [("", "missing"), ("garbage", "signature"), (body + "." + "0" * 64, "signature"),
                        (token(build_id="def456"), "different build"), (token(code=CODE + "//x"), "different sketch"),
                        (token(ttl=-1), "expired")]:
        stage, message = reject_stage(lambda: agent.flash("abc123", bad))
        assert stage == "approval" and reason in message, bad
    assert "upload" not in fake.commands() and "board" not in fake.commands()

    assert agent.flash("abc123", good)["upload_ok"] is True
    stage, message = reject_stage(lambda: agent.flash("abc123", good))      # single use
    assert stage == "approval" and "already used" in message
    assert fake.commands().count("upload") == 1


def test_flash_without_compile_is_refused(agent, monkeypatch):
    FakeTools(monkeypatch)
    assert reject_stage(lambda: agent.flash("abc123", token()))[0] == "compile"
    assert reject_stage(lambda: agent.flash("../x", token()))[0] == "compile"


# ---------- upload ----------

def test_successful_deployment(agent, monkeypatch):
    fake = FakeTools(monkeypatch)
    seen = []
    agent.on_event = lambda build_id, event: seen.append((event["stage"], event["status"]))
    agent.compile(artifact())
    r = agent.flash("abc123", token(), ["READY", "SOUND_DETECTED", "LED_ON", "LED_OFF"])
    ws = str(agent.workspace)
    assert r["status"] == "flashed" and r["upload_ok"] is True and r["mock"] is False and r["port"] == PORT
    assert fake.calls[-2] == [CLI, "upload", "--fqbn", "arduino:zephyr:unoq", "-p", PORT, "--input-dir",
                              f"{ws}/abc123/build", "--no-color", f"{ws}/abc123/learnlens_sketch"]
    assert fake.calls[-1] == [CLI, "monitor", "-p", PORT, "--fqbn", "arduino:zephyr:unoq", "--quiet"]
    assert r["runtime_log"] == ["READY", "SOUND_DETECTED", "LED_ON"] and r["logs_captured"] is True
    assert r["observed_events"] == ["READY", "SOUND_DETECTED", "LED_ON"]       # LED_OFF was not seen, so not claimed
    assert seen == [("validate", "ok"), ("compile", "started"), ("compile", "ok"), ("approval", "ok"),
                    ("device", "ok"), ("upload", "started"), ("upload", "ok"), ("logs", "started"), ("logs", "ok")]


def test_upload_failure_is_reported_and_can_be_retried(agent, monkeypatch):
    fake = FakeTools(monkeypatch, upload=(1, "Error: adb: device offline\nFailed uploading: uploading error: exit status 1"))
    agent.compile(artifact())
    stage, message = reject_stage(lambda: agent.flash("abc123", token()))
    assert stage == "upload" and "exited with 1" in message and "device offline" in message
    assert agent.logs("abc123")["uploaded"] is False and "monitor" not in fake.commands()

    fake.results["upload"] = (0, "Upload completed")                        # retry needs a fresh approval
    assert agent.flash("abc123", token())["status"] == "flashed"


def test_upload_timeout_and_silent_board_are_not_success(agent, monkeypatch):
    fake = FakeTools(monkeypatch, upload=(subprocess.TimeoutExpired("arduino-cli", 120), ""), monitor=())
    agent.compile(artifact())
    stage, message = reject_stage(lambda: agent.flash("abc123", token()))
    assert stage == "upload" and "did not finish" in message

    fake.results["upload"] = (0, "ok")
    r = agent.flash("abc123", token(), ["READY"])
    assert r["upload_ok"] is True and r["runtime_log"] == [] and r["observed_events"] == [] and r["logs_captured"] is False
    last = agent.logs("abc123")["events"][-1]
    assert (last["stage"], last["status"], last["message"]) == ("logs", "failed", "no output received from the board")


def test_mock_mode_runs_no_tool_but_still_needs_approval(monkeypatch):
    fake = FakeTools(monkeypatch)
    a = core.DeployAgent(tools.Config(cli=CLI, adb=None), mock=True)
    assert a.compile(artifact())["mock"] is True
    assert reject_stage(lambda: a.flash("abc123", ""))[0] == "approval"
    r = a.flash("abc123", token())
    assert r["status"] == "simulated" and r["mock"] is True and "no board was flashed" in r["log"]
    assert fake.calls == [] and a.device()["connected"] is False
    a.close()


# ---------- tools ----------

def test_only_allowlisted_executables_and_discovered_ports():
    config = tools.Config(cli=CLI, adb=None)
    for argv in (["/bin/sh", "-c", "id"], ["arduino-cli", "compile"], [CLI, "compile", "a\x00b"], []):
        with pytest.raises(tools.ToolError):
            tools.run(config, argv, 1)
    for port in ("/dev/cu.usbmodem1; rm -rf ~", "--config-file=/etc/x", "192.168.1.5", "/etc/passwd"):
        with pytest.raises(tools.ToolError):
            tools.upload_cmd(config, port, core.Path("/ws/a"), core.Path("/ws/b"))
    assert tools.monitor_cmd(tools.Config(cli=CLI, adb=None, log_transport="none"), PORT, "S1") is None
    assert tools.monitor_cmd(tools.Config(cli=CLI, adb="/fake/adb", log_transport="adb-app-cli"), PORT, "2E4A9F0113") == [
        "/fake/adb", "-s", "2E4A9F0113", "shell", "arduino-app-cli", "monitor"]
    assert "shell=True" not in open(tools.__file__).read().replace("shell=True is never used", "")


# ---------- HTTP API ----------

@pytest.fixture
def api(agent, monkeypatch):
    FakeTools(monkeypatch)
    srv = server.make_server(agent, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(method, path, body=None, headers=None):
        req = urllib.request.Request(base + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield call
    srv.shutdown()
    srv.server_close()


def test_http_api_end_to_end(api):
    assert api("GET", "/device")[1]["connected"] is True
    assert api("POST", "/compile", artifact())[1]["status"] == "ok"
    assert api("POST", "/flash", {"build_id": "abc123"}) == (403, {
        "error": "approval token missing: the user has not approved this sketch", "stage": "approval"})
    status, flashed = api("POST", "/flash", {"build_id": "abc123", "approval_token": token(), "expected_events": ["READY"]})
    assert status == 200 and flashed["status"] == "flashed" and flashed["observed_events"] == ["READY"]

    status, logs = api("GET", "/logs?build_id=abc123")
    assert status == 200 and logs["uploaded"] and logs["runtime_log"][0] == "READY"
    assert [e["stage"] for e in logs["events"]][:3] == ["validate", "compile", "compile"]
    assert api("GET", f"/logs?build_id=abc123&since={logs['next']}")[1]["events"] == []


def test_http_api_rejects_bad_callers_and_input(api, monkeypatch):
    assert api("POST", "/compile", artifact(sha256="0" * 64))[0] == 422
    assert api("POST", "/flash", {"build_id": "nope", "approval_token": "x"})[0] == 409
    assert api("GET", "/logs?build_id=nope")[0] == 422
    assert api("GET", "/etc/passwd")[0] == 404 and api("POST", "/shell", {"cmd": "id"})[0] == 404
    assert api("POST", "/compile", "not an object")[0] == 400
    assert api("POST", "/compile", {"pad": "x" * 70000})[0] == 413
    assert api("GET", "/device", headers={"Host": "evil.example.com"})[0] == 403         # DNS rebinding
    assert api("POST", "/compile", artifact(), headers={"Origin": "https://evil.example.com"})[0] == 403  # web page
    monkeypatch.setenv("LEARNLENS_AGENT_TOKEN", "s3cret")
    assert api("POST", "/compile", artifact())[0] == 401
    assert api("POST", "/compile", artifact(), headers={"X-Agent-Token": "s3cret"})[0] == 200
    assert server.HOST == "127.0.0.1"


# ---------- following board output after the upload ----------

def test_follow_logs_sends_only_lines_that_were_read(agent, monkeypatch):
    import learnlens_agent

    fake = FakeTools(monkeypatch, monitor=("READY",))
    agent.compile(artifact())
    agent.flash("abc123", token(), ["READY", "SOUND_DETECTED"])
    fake.monitor = ("SOUND_DETECTED", "LED_ON")
    sent = []
    job = {"job_id": "j1", "build_id": "abc123", "expected_serial_events": ["READY", "SOUND_DETECTED"]}
    n = learnlens_agent.follow_logs(lambda path, body: sent.append((path, body["stage"], body["message"])) or (200, None),
                                    agent, job, seconds=5, chunk_s=0.1)
    assert [m for _, _, m in sent] == ["READY", "SOUND_DETECTED", "LED_ON"] and n == 3   # stopped early: all expected seen
    assert all(path == "/agent/jobs/j1/events" and stage == "runtime" for path, stage, _ in sent)


def test_led_matrix_header_is_accepted(agent, monkeypatch):
    FakeTools(monkeypatch)
    code = "#include <Arduino_RouterBridge.h>\n#include <Arduino_LED_Matrix.h>\nvoid setup(){}\nvoid loop(){}\n"
    assert agent.compile(artifact(code=code))["status"] == "ok"


# ---------- the board is plugged in but its Linux side is not up yet ----------

@pytest.fixture
def adb_agent():
    a = core.DeployAgent(tools.Config(cli=CLI, adb=ADB, log_capture_s=0, ready_timeout_s=0.4, ready_poll_s=0.01))
    yield a
    a.close()


def test_upload_waits_for_a_board_that_is_still_starting(adb_agent, monkeypatch):
    fake = FakeTools(monkeypatch)
    fake.adb_states = ["offline", "offline", "device"]
    seen = []
    adb_agent.on_event = lambda _, e: seen.append((e["stage"], e["status"], e["message"]))
    adb_agent.compile(artifact())
    r = adb_agent.flash("abc123", token())
    assert r["status"] == "flashed" and fake.commands().count("upload") == 1
    assert ("device", "started", "the board is still starting up, waiting for it") in seen
    assert ("device", "ok", "the board is ready") in seen
    assert [ADB, "-s", "2E4A9F0113", "get-state"] in fake.calls
    assert adb_agent.device()["boards"][0]["adb_state"] == "device"


def test_a_board_that_never_gets_ready_is_reported_and_nothing_is_uploaded(adb_agent, monkeypatch):
    fake = FakeTools(monkeypatch)
    fake.adb_states = ["offline"]
    adb_agent.compile(artifact())
    stage, message = reject_stage(lambda: adb_agent.flash("abc123", token()))
    assert stage == "device" and "not ready for uploads (adb: offline)" in message and "wait about a minute" in message
    assert "upload" not in fake.commands() and [ADB, "reconnect", "offline"] in fake.calls
    assert "still starting up" in adb_agent.device()["problem"]


def test_upload_that_loses_the_board_is_retried_once(adb_agent, monkeypatch):
    fake = FakeTools(monkeypatch, upload=[(1, "adb: error: device offline\nFailed uploading"), (0, "Upload completed")])
    adb_agent.compile(artifact())
    assert adb_agent.flash("abc123", token())["status"] == "flashed"
    assert fake.commands().count("upload") == 2

    fake.results["upload"] = [(1, "adb: error: device offline")]             # still failing after the retry
    adb_agent.compile(artifact())
    stage, message = reject_stage(lambda: adb_agent.flash("abc123", token()))
    assert stage == "upload" and "device offline" in message and fake.commands().count("upload") == 4

    fake.results["upload"] = [(1, "Error: verification failed at 0x08100000")]  # a different failure is not retried
    adb_agent.compile(artifact())
    assert reject_stage(lambda: adb_agent.flash("abc123", token()))[0] == "upload"
    assert fake.commands().count("upload") == 5
