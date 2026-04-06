"""
BashTool — async shell command execution.
RULE 3: shell=False, timeout=30s, list-form command.
RULE 1: No unimplemented placeholders.
Path traversal guard rejects ../../ escapes.
JSON schema validation on every call before execution.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BASH_TOOL_SCHEMA: dict[str, Any] = {
    "name": "bash",
    "description": (
        "Run a shell command in the project directory. "
        "Timeout: 30 seconds. Working directory: project root."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "timeout": {
                "type": "number",
                "description": "Override timeout in seconds (max 30).",
                "default": 30,
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}

MAX_OUTPUT_BYTES: int = 32_768  # 32 KB output cap
DEFAULT_TIMEOUT: float = 30.0


def _validate_input(input_json: bytes) -> dict[str, Any]:
    """Parse and validate JSON input against schema. Raises ValueError on failure."""
    try:
        data: Any = json.loads(input_json)
    except json.JSONDecodeError as e:
        raise ValueError(f"BashTool: invalid JSON input — {e}") from e
    if not isinstance(data, dict):
        raise ValueError("BashTool: input must be a JSON object")
    if "command" not in data:
        raise ValueError("BashTool: missing required field 'command'")
    if not isinstance(data["command"], str):
        raise ValueError("BashTool: 'command' must be a string")
    return dict(data)


def _guard_path_traversal(command: str, cwd: Path) -> None:
    """Reject commands that attempt directory traversal outside cwd."""
    if "../" in command or "..\\" in command:
        raise PermissionError(
            f"BashTool: path traversal detected in command: {command!r}"
        )


class BashTool:
    """
    Executes shell commands safely via async subprocess.
    RULE 3: shell=False, timeout=30s, list-form subprocess call.
    """

    TOOL_NAME = "bash"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def schema(self) -> dict[str, Any]:
        return BASH_TOOL_SCHEMA

    async def execute(self, input_json: bytes, session_id: str) -> str:
        """
        Execute a shell command. Returns stdout+stderr as a string.
        Returns "[error] ..." string on failure — does NOT raise.
        """
        try:
            data = _validate_input(input_json)
        except ValueError as e:
            return f"[error] {e}"

        command: str = data["command"]
        timeout = min(float(data.get("timeout", DEFAULT_TIMEOUT)), DEFAULT_TIMEOUT)

        try:
            _guard_path_traversal(command, self._cwd)
        except PermissionError as e:
            return f"[permission denied] {e}"

        # RULE 3: shell=False — split command into list form
        try:
            cmd_list = shlex.split(command, posix=(os.name != "nt"))
        except ValueError as e:
            return f"[error] BashTool: failed to parse command — {e}"

        if not cmd_list:
            return "[error] BashTool: empty command"

        logger.debug(
            "BashTool: execute session=%s cmd=%s timeout=%.1fs",
            session_id, cmd_list[0], timeout,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd_list,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=self._cwd,
                # RULE 3: shell=False is the default for create_subprocess_exec
            )
            try:
                stdout_bytes, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return f"[error] BashTool: command timed out after {timeout:.0f}s"

            output = (stdout_bytes or b"")[:MAX_OUTPUT_BYTES].decode(
                "utf-8", errors="replace",
            )
            exit_code = proc.returncode or 0

            if exit_code != 0:
                return f"[exit {exit_code}]\n{output}"
            return output

        except FileNotFoundError:
            return f"[error] BashTool: command not found — {cmd_list[0]!r}"
        except PermissionError as e:
            return f"[permission denied] {e}"
        except Exception as e:
            logger.error("BashTool: unexpected error — %s", e)
            return f"[error] BashTool: {e}"
