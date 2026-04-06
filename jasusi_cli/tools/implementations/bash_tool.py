"""BashTool — RULE 3: subprocess with timeout=30s, shell=False, list argv only."""

from __future__ import annotations

import hashlib
import logging
import shlex
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

BASH_TIMEOUT_SECONDS: int = 30
MAX_OUTPUT_CHARS: int = 8_192


class BashTool:
    NAME: str = "bash"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def execute(self, input_data: dict[str, object], session_id: str) -> str:
        """
        Execute a shell command.
        RULE 3: shell=False always. Timeout 30s always.
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

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=BASH_TIMEOUT_SECONDS,
                shell=False,       # RULE 3: NEVER shell=True
                cwd=str(self._cwd),
            )
            output = result.stdout + result.stderr
            if len(output) > MAX_OUTPUT_CHARS:
                output = output[:MAX_OUTPUT_CHARS] + "\n[truncated]"
            return output if output else f"[exit code {result.returncode}]"

        except subprocess.TimeoutExpired:
            return f"[error] Command timed out after {BASH_TIMEOUT_SECONDS}s"
        except FileNotFoundError as e:
            return f"[error] Command not found: {e}"
        except PermissionError as e:
            return f"[error] Permission denied: {e}"
        except OSError as e:
            return f"[error] OS error: {e}"
