"""Compile-only check of a sketch with arduino-cli and the official UNO Q core.

This module can only compile. It has no upload path: flashing happens in the Mac deploy agent,
and only for a build the user approved.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from .models import CompileReport, CompileStatus

IDE_BUNDLED_CLI = "/Applications/Arduino IDE.app/Contents/Resources/app/lib/backend/resources/arduino-cli"
TIMEOUT_S = float(os.getenv("LEARNLENS_COMPILE_TIMEOUT", "300"))
MAX_LOG_CHARS = 6000
# Signs that the machine is not set up, as opposed to the sketch being wrong.
SETUP_PROBLEMS = ("Invalid FQBN", "not installed", "platform not found", "No such file or directory: 'arduino-cli'")
FORBIDDEN_ARGS = {"upload", "-u", "--upload", "-t", "--verify", "burn-bootloader", "-p", "--port"}


def find_cli() -> Optional[str]:
    explicit = os.getenv("LEARNLENS_ARDUINO_CLI")
    if explicit:
        return explicit if explicit != "none" and Path(explicit).exists() else None
    return shutil.which("arduino-cli") or (IDE_BUNDLED_CLI if Path(IDE_BUNDLED_CLI).exists() else None)


def compile_command(cli: str, fqbn: str, sketch_dir: Path) -> List[str]:
    command = [cli, "compile", "--fqbn", fqbn, "--warnings", "default", "--no-color", str(sketch_dir)]
    assert not FORBIDDEN_ARGS & set(command), "the backend compiles only, it never uploads"
    return command


class ArduinoCliCompiler:
    def compile(self, sketch_dir: Path, fqbn: str) -> CompileReport:
        cli = find_cli()
        if cli is None:
            return CompileReport(status=CompileStatus.not_run, fqbn=fqbn,
                                 detail="arduino-cli was not found on this machine")
        command = compile_command(cli, fqbn, sketch_dir)
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return CompileReport(status=CompileStatus.not_run, fqbn=fqbn, command=command,
                                 detail=f"compile did not finish within {TIMEOUT_S:.0f} s")
        except OSError as e:
            return CompileReport(status=CompileStatus.not_run, fqbn=fqbn, command=command, detail=str(e))
        log = re.sub(r"\x1b\[[0-9;]*m", "", run.stdout + run.stderr)[-MAX_LOG_CHARS:]
        if run.returncode == 0:
            return CompileReport(status=CompileStatus.ok, fqbn=fqbn, command=command, log=log)
        if any(marker in log for marker in SETUP_PROBLEMS):
            return CompileReport(status=CompileStatus.not_run, fqbn=fqbn, command=command, log=log,
                                 detail="the board core or a library is not installed (see docs/compile-contract.md)")
        return CompileReport(status=CompileStatus.failed, fqbn=fqbn, command=command, log=log)
