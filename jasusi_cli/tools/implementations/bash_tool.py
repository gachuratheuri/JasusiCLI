"""BashTool — RULE 3: subprocess with timeout=30s, shell=False, list argv only."""

from __future__ import annotations

import hashlib
import logging
import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BASH_TIMEOUT_SECONDS: int = 30
MAX_OUTPUT_CHARS: int = 8_192


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        else:
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


class BashTool:
    NAME: str = "bash"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def execute(self, input_data: dict[str, object], session_id: str) -> str:
        """
        Execute a shell command.
        RULE 3: shell=False always. Timeout 30s always. Process tree reaped on timeout.
        RULE 9: command is hashed before logging.
        """
        command = str(input_data.get("command", ""))
        if not command.strip():
            return "[error] Empty command"

        # RULE 9: hash before logging
        cmd_hash = hashlib.sha256(command.encode()).hexdigest()[:16]
        logger.info("BashTool execute: session=%s cmd_hash=%s", session_id, cmd_hash)

        try:
            argv = shlex.split(command)
        except ValueError as e:
            return f"[error] Could not parse command: {e}"

        popen_kwargs: dict[str, object] = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
            "cwd": str(self._cwd),
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        try:
            proc = subprocess.Popen(argv, **popen_kwargs)
            stdout, stderr = proc.communicate(timeout=BASH_TIMEOUT_SECONDS)
            output = (stdout or "") + (stderr or "")
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n[truncated]"
            return output if output else f"[exit code {proc.returncode}]"
        except subprocess.TimeoutExpired:
            _kill_process_tree(proc)
            return f"[error] Command timed out after {BASH_TIMEOUT_SECONDS}s"
        except FileNotFoundError as e:
            return f"[error] Command not found: {e}"
        except PermissionError as e:
            return f"[error] Permission denied: {e}"
        except OSError as e:
            return f"[error] OS error: {e}"
