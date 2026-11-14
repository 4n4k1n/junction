"""The only place that starts processes.

Every command is a fixed argument list built here from validated values: the arduino-cli (and
optionally adb) executable resolved once at startup, the fixed FQBN, a port that came from device
discovery, and paths inside the agent's workspace. Nothing from a sketch, a model or an HTTP
request is ever passed to a shell. shell=True is never used.

Workflow, read from the installed core arduino:zephyr 1.1.0 (boards.txt, platform.txt):
  compile:  arduino-cli compile --fqbn arduino:zephyr:unoq (arm-zephyr-eabi toolchain)
  upload:   arduino-cli upload  --fqbn arduino:zephyr:unoq -p <port>, which runs the core's
            `remoteocd upload --adb-path <adb> -s <port serialNumber> ...`: OpenOCD on the board's
            Linux side, reached over USB with adb. The board is matched by USB VID 0x2341 PID 0x0078.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

IDE_BUNDLED_CLI = "/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"
FQBN = "arduino:zephyr:unoq"
CORE = "arduino:zephyr"
USB_VID, USB_PID = 0x2341, 0x0078  # unoq.upload_port.0 in boards.txt
PORT_RE = re.compile(r"^/dev/(cu|tty)\.[A-Za-z0-9._-]{1,64}$")
SERIAL_RE = re.compile(r"^[A-Za-z0-9]{4,64}$")


class ToolError(Exception):
    pass


def resolve_cli() -> Optional[str]:
    explicit = os.getenv("LEARNLENS_ARDUINO_CLI")
    candidates = [explicit] if explicit else [shutil.which("arduino-cli"), IDE_BUNDLED_CLI]
    for c in candidates:
        if c and c != "none" and os.path.isabs(c) and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def resolve_adb() -> Optional[str]:
    """The adb that ships with the Arduino core, for the optional adb log transport."""
    base = Path.home() / "Library/Arduino15/packages/arduino/tools/adb"
    found = sorted(base.glob("*/adb")) if base.is_dir() else []
    return str(found[-1]) if found else None


@dataclass
class Config:
    cli: Optional[str] = field(default_factory=resolve_cli)
    adb: Optional[str] = field(default_factory=resolve_adb)
    fqbn: str = FQBN
    allowed_includes: Tuple[str, ...] = ("Arduino_RouterBridge.h", "Arduino_LED_Matrix.h")
    compile_timeout_s: float = float(os.getenv("LEARNLENS_COMPILE_TIMEOUT", "300"))
    upload_timeout_s: float = float(os.getenv("LEARNLENS_UPLOAD_TIMEOUT", "120"))
    log_transport: str = os.getenv("LEARNLENS_LOG_TRANSPORT", "cli-monitor")  # cli-monitor | adb-app-cli | none
    log_capture_s: float = float(os.getenv("LEARNLENS_LOG_CAPTURE_S", "6"))
    # The board's Linux side needs a while after power-up before adb (and with it the upload) works.
    ready_timeout_s: float = float(os.getenv("LEARNLENS_BOARD_READY_TIMEOUT", "90"))
    ready_poll_s: float = 2.0

    @property
    def executables(self) -> set:
        return {e for e in (self.cli, self.adb) if e}


def run(config: Config, argv: List[str], timeout: float) -> Tuple[int, str]:
    """Run one allowlisted executable to completion. Returns (exit code, stdout + stderr)."""
    _check(config, argv)
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, shell=False,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ToolError(f"{Path(argv[0]).name} {argv[1]} did not finish within {timeout:.0f} s")
    except OSError as e:
        raise ToolError(f"could not start {Path(argv[0]).name}: {e}")
    return done.returncode, re.sub(r"\x1b\[[0-9;]*m", "", (done.stdout or "") + (done.stderr or ""))


def read_lines(config: Config, argv: List[str], seconds: float, max_lines: int = 200) -> List[str]:
    """Run a monitor command for `seconds` and return the lines it printed. Stdin stays open and empty."""
    _check(config, argv)
    try:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, shell=False)
    except OSError as e:
        raise ToolError(f"could not start {Path(argv[0]).name}: {e}")
    lines: List[str] = []

    def pump() -> None:
        for line in proc.stdout:
            if len(lines) < max_lines:
                lines.append(line.rstrip("\r\n")[:300])

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and proc.poll() is None:
        time.sleep(0.05)
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    reader.join(timeout=2)
    return lines


def _check(config: Config, argv: List[str]) -> None:
    if not argv or argv[0] not in config.executables:
        raise ToolError("executable is not on the allowlist")
    if not all(isinstance(a, str) and a and "\x00" not in a for a in argv):
        raise ToolError("invalid argument")


# ---------- fixed command lines ----------

def compile_cmd(config: Config, sketch_dir: Path, build_dir: Path) -> List[str]:
    return [config.cli, "compile", "--fqbn", config.fqbn, "--warnings", "default", "--no-color",
            "--build-path", str(build_dir), str(sketch_dir)]


def upload_cmd(config: Config, port: str, sketch_dir: Path, build_dir: Path) -> List[str]:
    if not PORT_RE.match(port):
        raise ToolError(f"refusing port {port!r}")
    return [config.cli, "upload", "--fqbn", config.fqbn, "-p", port, "--input-dir", str(build_dir),
            "--no-color", str(sketch_dir)]


def board_list_cmd(config: Config) -> List[str]:
    return [config.cli, "board", "list", "--json"]


def core_list_cmd(config: Config) -> List[str]:
    return [config.cli, "core", "list", "--json"]


def adb_state_cmd(config: Config, serial: str) -> List[str]:
    """Prints "device" once the board's Linux side accepts adb connections, which the upload needs."""
    if not SERIAL_RE.match(serial):
        raise ToolError(f"refusing serial number {serial!r}")
    return [config.adb, "-s", serial, "get-state"]


def adb_reconnect_cmd(config: Config) -> List[str]:
    return [config.adb, "reconnect", "offline"]


def monitor_cmd(config: Config, port: str, serial: str) -> Optional[List[str]]:
    """Command that prints the sketch's Monitor output, or None when log capture is off or impossible."""
    if config.log_transport == "cli-monitor" and PORT_RE.match(port):
        return [config.cli, "monitor", "-p", port, "--fqbn", config.fqbn, "--quiet"]
    if config.log_transport == "adb-app-cli" and config.adb and SERIAL_RE.match(serial):
        return [config.adb, "-s", serial, "shell", "arduino-app-cli", "monitor"]
    return None


# ---------- parsing tool output ----------

def find_boards(board_list_json: str, fqbn: str = FQBN) -> List[dict]:
    """UNO Q boards in `arduino-cli board list --json`, matched by FQBN or by USB VID/PID."""
    try:
        ports = json.loads(board_list_json).get("detected_ports", [])
    except (ValueError, AttributeError):
        raise ToolError("could not read the board list from arduino-cli")
    boards = []
    for entry in ports:
        port = entry.get("port") or {}
        props = {str(k).lower(): str(v) for k, v in (port.get("properties") or {}).items()}
        matches = [b.get("fqbn") for b in entry.get("matching_boards") or []]
        try:
            usb = (int(props.get("vid", "0"), 16), int(props.get("pid", "0"), 16))
        except ValueError:
            usb = (0, 0)
        if fqbn in matches or usb == (USB_VID, USB_PID):
            boards.append({"port": str(port.get("address", "")), "protocol": str(port.get("protocol", "")),
                           "serial_number": props.get("serialnumber", ""), "fqbn": fqbn})
    return boards


def core_installed(core_list_json: str) -> Optional[str]:
    try:
        platforms = json.loads(core_list_json).get("platforms", [])
    except (ValueError, AttributeError):
        return None
    for p in platforms:
        if p.get("id") == CORE:
            return str(p.get("installed_version") or p.get("installed") or "installed")
    return None


DIAGNOSTIC_RE = re.compile(r"^(?P<file>[^\s:][^:\n]*):(?P<line>\d+):(?P<col>\d+): (?P<sev>fatal error|error|warning): (?P<msg>.*)$", re.M)


def parse_diagnostics(log: str) -> List[dict]:
    return [{"file": Path(m["file"]).name, "line": int(m["line"]), "column": int(m["col"]),
             "severity": "error" if "error" in m["sev"] else "warning", "message": m["msg"].strip()[:300]}
            for m in DIAGNOSTIC_RE.finditer(log)][:30]
