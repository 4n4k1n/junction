"""The deploy pipeline: validate the artifact, compile, check the approval, find the board,
upload, read back logs. One DeployAgent owns one temporary workspace; nothing is read or written
outside it.

Upload success is taken from the upload tool's exit code and nothing else. Runtime log lines are
reported only if they were actually read from the board.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from . import approval, tools

BUILD_ID_RE = re.compile(r"^[0-9a-f]{6,32}$")
FILENAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}\.ino$")
MAX_CODE_CHARS = 20000
SIMULATED = "SIMULATED (--mock): no tool was run and no board was flashed"


class Rejected(Exception):
    """The request cannot be carried out. `stage` says where it stopped."""

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def _strip_comments(code: str) -> str:
    out, i, n = [], 0, len(code)
    while i < n:
        if code.startswith("//", i):
            j = code.find("\n", i)
            i = n if j == -1 else j
        elif code.startswith("/*", i):
            j = code.find("*/", i + 2)
            out.append(" ")
            i = n if j == -1 else j + 2
        elif code[i] == '"':
            j = i + 1
            while j < n and code[j] not in '"\n':
                j += 2 if code[j] == "\\" else 1
            out.append(code[i:j + 1])
            i = j + 1
        else:
            out.append(code[i])
            i += 1
    return "".join(out)


class DeployAgent:
    def __init__(self, config: Optional[tools.Config] = None, mock: bool = False,
                 on_event: Optional[Callable[[str, dict], None]] = None) -> None:
        self.config = config or tools.Config()
        self.mock = mock
        self.on_event = on_event
        self.workspace = Path(tempfile.mkdtemp(prefix="learnlens-agent-")).resolve()
        self.builds: Dict[str, dict] = {}
        self.used_nonces: set = set()
        self.lock = threading.Lock()  # one compile or upload at a time

    def close(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)

    # ---------- status ----------

    def _event(self, build_id: str, stage: str, status: str, message: str = "") -> None:
        event = {"stage": stage, "status": status, "message": message[:500], "ts": time.time()}
        self.builds.setdefault(build_id, {"events": [], "runtime_log": []})["events"].append(event)
        if self.on_event:
            try:
                self.on_event(build_id, event)
            except Exception:
                pass  # a backend hiccup must not stop a deploy

    def device(self) -> dict:
        """Toolchain and board status. Never raises."""
        info = {"mock": self.mock, "fqbn": self.config.fqbn, "connected": False, "boards": [],
                "toolchain": {"arduino_cli": self.config.cli, "core": None, "ready": False}, "problem": ""}
        if self.mock:
            info["problem"] = SIMULATED
            return info
        if not self.config.cli:
            info["problem"] = "arduino-cli not found (install the Arduino IDE or set LEARNLENS_ARDUINO_CLI)"
            return info
        try:
            _, cores = tools.run(self.config, tools.core_list_cmd(self.config), 30)
            info["toolchain"]["core"] = tools.core_installed(cores)
            info["toolchain"]["ready"] = info["toolchain"]["core"] is not None
            if not info["toolchain"]["ready"]:
                info["problem"] = "board core missing: arduino-cli core install arduino:zephyr"
            _, listing = tools.run(self.config, tools.board_list_cmd(self.config), 30)
            info["boards"] = tools.find_boards(listing, self.config.fqbn)
            for board in info["boards"]:
                board["adb_state"] = self.board_state(board["serial_number"])  # "device" = ready for uploads
            info["connected"] = bool(info["boards"])
            if info["toolchain"]["ready"] and not info["connected"]:
                info["problem"] = "no Arduino UNO Q found on USB"
            elif info["boards"] and info["boards"][0]["adb_state"] in ("offline", "not found"):
                info["problem"] = "the board is plugged in but still starting up (not ready for uploads yet)"
        except tools.ToolError as e:
            info["problem"] = str(e)
        return info

    # ---------- artifact ----------

    def _paths(self, build_id: str, filename: str) -> Dict[str, Path]:
        name = filename[:-4]
        root = (self.workspace / build_id).resolve()
        paths = {"root": root, "sketch_dir": root / name, "build_dir": root / "build", "ino": root / name / filename}
        if any(self.workspace not in p.parents for p in paths.values()):
            raise Rejected("validate", "path escapes the workspace")
        return paths

    def validate(self, artifact: dict) -> dict:
        """Check a project artifact from the backend. Returns the normalized artifact or raises Rejected."""
        if not isinstance(artifact, dict) or not isinstance(artifact.get("sketch"), dict):
            raise Rejected("validate", "artifact must be {build_id, sketch: {filename, code, sha256}}")
        build_id, sketch = artifact.get("build_id"), artifact["sketch"]
        filename, code, sha = sketch.get("filename"), sketch.get("code"), sketch.get("sha256")
        if not isinstance(build_id, str) or not BUILD_ID_RE.match(build_id):
            raise Rejected("validate", "build_id must be 6-32 lowercase hex characters")
        if not isinstance(filename, str) or not FILENAME_RE.match(filename):
            raise Rejected("validate", "sketch filename must look like learnlens_sketch.ino")
        if not isinstance(code, str) or not code.strip() or len(code) > MAX_CODE_CHARS:
            raise Rejected("validate", f"sketch code must be 1-{MAX_CODE_CHARS} characters")
        if hashlib.sha256(code.encode()).hexdigest() != sha:
            raise Rejected("validate", "sketch does not match its sha256")
        if artifact.get("fqbn") not in (None, self.config.fqbn):
            raise Rejected("validate", f"this agent only builds for {self.config.fqbn}")
        # The preprocessor can read any file on this Mac. Only the approved headers may be included.
        bare = _strip_comments(code)
        if "%:" in bare or "??=" in bare:
            raise Rejected("validate", "digraphs and trigraphs are not allowed")
        for line in bare.splitlines():
            line = line.strip()
            if not line.startswith("#"):
                continue
            m = re.fullmatch(r"#\s*include\s*<([\w./-]+)>", line)
            if not m or m.group(1) not in self.config.allowed_includes:
                raise Rejected("validate", f"preprocessor line not allowed: {line[:80]}")
        return {"build_id": build_id, "filename": filename, "code": code, "sha256": sha}

    def compile(self, artifact: dict) -> dict:
        """Validate, write into the workspace, compile. Returns {status: ok|failed, diagnostics, log, sha256}."""
        try:
            a = self.validate(artifact)
        except Rejected as e:
            if isinstance(artifact, dict) and isinstance(artifact.get("build_id"), str) and BUILD_ID_RE.match(artifact["build_id"]):
                self._event(artifact["build_id"], "validate", "failed", str(e))
            raise
        build_id = a["build_id"]
        with self.lock:
            self._event(build_id, "validate", "ok", "artifact accepted")
            paths = self._paths(build_id, a["filename"])
            shutil.rmtree(paths["root"], ignore_errors=True)
            paths["sketch_dir"].mkdir(parents=True)
            paths["build_dir"].mkdir()
            paths["ino"].write_text(a["code"])
            state = self.builds[build_id]
            state.update(sha256=a["sha256"], paths=paths, compiled=False, uploaded=False)

            self._event(build_id, "compile", "started", f"compiling for {self.config.fqbn}")
            if self.mock:
                state["compiled"] = True
                self._event(build_id, "compile", "ok", SIMULATED)
                return {"status": "ok", "diagnostics": [], "log": SIMULATED, "sha256": a["sha256"], "mock": True}
            if not self.config.cli:
                self._event(build_id, "compile", "failed", "arduino-cli not found")
                raise Rejected("compile", "arduino-cli not found on this Mac")
            try:
                code, log = tools.run(self.config, tools.compile_cmd(self.config, paths["sketch_dir"], paths["build_dir"]),
                                      self.config.compile_timeout_s)
            except tools.ToolError as e:
                self._event(build_id, "compile", "failed", str(e))
                raise Rejected("compile", str(e))
            log = log.replace(str(self.workspace), "<workspace>")
            diagnostics = tools.parse_diagnostics(log)
            state["compiled"] = code == 0
            errors = sum(d["severity"] == "error" for d in diagnostics)
            self._event(build_id, "compile", "ok" if code == 0 else "failed",
                        "compiled" if code == 0 else f"{errors or 'compile'} error(s)")
            return {"status": "ok" if code == 0 else "failed", "diagnostics": diagnostics, "log": log[-4000:],
                    "sha256": a["sha256"], "mock": False}

    def has_compiled(self, build_id: str, sketch: dict) -> bool:
        """True when this agent already compiled exactly this sketch (before the approval)."""
        state = self.builds.get(build_id) or {}
        code, sha = sketch.get("code"), sketch.get("sha256")
        return bool(state.get("compiled")) and isinstance(code, str) and state.get("sha256") == sha \
            and hashlib.sha256(code.encode()).hexdigest() == sha

    # ---------- upload ----------

    def flash(self, build_id: str, approval_token: str, expected_events: Optional[List[str]] = None) -> dict:
        """Upload a compiled build. Raises Rejected unless it was compiled, approved, and a board is there."""
        state = self.builds.get(build_id) if isinstance(build_id, str) else None
        if not state or not state.get("compiled"):
            raise Rejected("compile", "this build has not been compiled successfully by this agent")
        with self.lock:
            try:
                approval.verify(approval_token, build_id, state["sha256"], self.used_nonces)
            except approval.ApprovalError as e:
                self._event(build_id, "approval", "failed", str(e))
                raise Rejected("approval", str(e))
            self._event(build_id, "approval", "ok", "approval token accepted")

            if self.mock:
                self._event(build_id, "upload", "ok", SIMULATED)
                return {"status": "simulated", "upload_ok": True, "mock": True, "port": "", "log": SIMULATED,
                        "runtime_log": [], "observed_events": [], "logs_captured": False}

            try:
                _, listing = tools.run(self.config, tools.board_list_cmd(self.config), 30)
                boards = tools.find_boards(listing, self.config.fqbn)
            except tools.ToolError as e:
                self._event(build_id, "device", "failed", str(e))
                raise Rejected("device", str(e))
            if not boards:
                self._event(build_id, "device", "failed", "no Arduino UNO Q found on USB")
                raise Rejected("device", "no Arduino UNO Q found on USB. Plug it in with a data cable and retry.")
            board, paths = boards[0], state["paths"]
            self._event(build_id, "device", "ok", f"UNO Q on {board['port']}")
            self._wait_until_ready(build_id, board)

            code, log = self._upload(build_id, board, paths)
            if code != 0 and re.search(r"offline|not found|no devices", log, re.I):
                # The board dropped off adb during the upload (typical right after plugging in): one more try.
                self._event(build_id, "upload", "failed", "the board was not reachable; trying once more")
                self._wait_until_ready(build_id, board)
                code, log = self._upload(build_id, board, paths)
            if code != 0:
                self._event(build_id, "upload", "failed", f"upload tool exited with {code}")
                raise Rejected("upload", f"upload tool exited with {code}:\n{log}")
            state["uploaded"], state["board"] = True, board
            self._event(build_id, "upload", "ok", "upload tool reported success")

            lines = self._capture(build_id, self.config.log_capture_s)
            observed = [e for e in (expected_events or []) if any(e in line for line in lines)]
            return {"status": "flashed", "upload_ok": True, "mock": False, "port": board["port"], "log": log,
                    "runtime_log": lines, "observed_events": observed, "logs_captured": bool(lines)}

    def _upload(self, build_id: str, board: dict, paths: Dict[str, Path]):
        self._event(build_id, "upload", "started", f"uploading to {board['port']}")
        try:
            code, log = tools.run(self.config, tools.upload_cmd(self.config, board["port"], paths["sketch_dir"],
                                                                paths["build_dir"]), self.config.upload_timeout_s)
        except tools.ToolError as e:
            self._event(build_id, "upload", "failed", str(e))
            raise Rejected("upload", str(e))
        return code, log.replace(str(self.workspace), "<workspace>")[-4000:]

    def board_state(self, serial: str) -> str:
        """adb's view of the board: "device" when uploads can work, else "offline", "not found" or "unknown"."""
        if not self.config.adb or not tools.SERIAL_RE.match(serial or ""):
            return "unknown"
        try:
            code, out = tools.run(self.config, tools.adb_state_cmd(self.config, serial), 10)
        except tools.ToolError:
            return "unknown"
        out = out.strip().lower()
        return "device" if code == 0 and out == "device" else "offline" if "offline" in out else "not found"

    def _wait_until_ready(self, build_id: str, board: dict) -> None:
        """The serial port shows up before the board's Linux side is up. Uploads go through that side
        (adb), so wait for it instead of failing. Raises Rejected("device") when it never comes up."""
        serial = board["serial_number"]
        state = self.board_state(serial)
        if state in ("device", "unknown"):  # unknown: no adb to ask, let the upload tool decide
            return
        self._event(build_id, "device", "started", "the board is still starting up, waiting for it")
        deadline = time.monotonic() + self.config.ready_timeout_s
        nudged = False
        while time.monotonic() < deadline:
            time.sleep(self.config.ready_poll_s)
            state = self.board_state(serial)
            if state == "device":
                self._event(build_id, "device", "ok", "the board is ready")
                return
            if not nudged and time.monotonic() > deadline - self.config.ready_timeout_s / 2:
                nudged = True  # stuck offline for a while: ask adb to reconnect once
                try:
                    tools.run(self.config, tools.adb_reconnect_cmd(self.config), 10)
                except tools.ToolError:
                    pass
        message = (f"the board is plugged in but not ready for uploads (adb: {state}). Unplug it, plug it back in, "
                   "wait about a minute for it to start, then retry.")
        self._event(build_id, "device", "failed", message)
        raise Rejected("device", message)

    # ---------- logs ----------

    def _capture(self, build_id: str, seconds: float) -> List[str]:
        """Read the board's log output for a while. Returns only what was actually received."""
        state = self.builds[build_id]
        board = state.get("board")
        command = tools.monitor_cmd(self.config, board["port"], board["serial_number"]) if board else None
        if command is None or seconds <= 0:
            return []
        self._event(build_id, "logs", "started", f"reading board output for {seconds:.0f} s")
        try:
            lines = tools.read_lines(self.config, command, seconds)
        except tools.ToolError as e:
            self._event(build_id, "logs", "failed", str(e))
            return []
        state["runtime_log"] += lines
        self._event(build_id, "logs", "ok" if lines else "failed",
                    f"{len(lines)} line(s) read" if lines else "no output received from the board")
        return lines

    def logs(self, build_id: str, since: int = 0, capture_s: float = 0) -> dict:
        """Status events and runtime log for a build. `capture_s` reads the board again first (uploaded builds only)."""
        state = self.builds.get(build_id)
        if state is None:
            raise Rejected("validate", "unknown build")
        if capture_s > 0 and state.get("uploaded") and not self.mock:
            with self.lock:
                self._capture(build_id, min(capture_s, 15))
        events = state["events"]
        return {"build_id": build_id, "events": events[max(since, 0):], "next": len(events),
                "runtime_log": state["runtime_log"], "uploaded": bool(state.get("uploaded"))}
