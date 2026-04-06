"""
FileReadTool — reads file content with 250-line head limit.
Path traversal guard rejects ../../ escapes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FILE_READ_SCHEMA: dict[str, Any] = {
    "name": "file_read",
    "description": "Read a file from the project directory. Returns first 250 lines.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the file, relative to the project root.",
            },
            "line_count": {
                "type": "integer",
                "description": "Number of lines to read (max 250, default 250).",
                "default": 250,
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    },
}

MAX_LINES: int = 250


class FileReadTool:
    """Reads file content with 250-line head limit and path traversal guard."""

    TOOL_NAME = "file_read"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def schema(self) -> dict[str, Any]:
        return FILE_READ_SCHEMA

    async def execute(self, input_json: bytes, session_id: str) -> str:
        try:
            data: Any = json.loads(input_json)
        except json.JSONDecodeError as e:
            return f"[error] FileReadTool: invalid JSON — {e}"

        if not isinstance(data, dict):
            return "[error] FileReadTool: input must be a JSON object"

        raw_path: str = str(data.get("path", ""))
        if not raw_path:
            return "[error] FileReadTool: missing required field 'path'"

        line_count = min(int(data.get("line_count", MAX_LINES)), MAX_LINES)

        if "../" in raw_path or "..\\" in raw_path:
            return (
                f"[permission denied] FileReadTool:"
                f" path traversal detected — {raw_path!r}"
            )

        target = (self._cwd / raw_path).resolve()
        try:
            cwd_resolved = self._cwd.resolve()
            target.relative_to(cwd_resolved)
        except ValueError:
            return (
                f"[permission denied] FileReadTool:"
                f" path outside project root — {raw_path!r}"
            )

        if not target.exists():
            return f"[error] FileReadTool: file not found — {raw_path!r}"
        if not target.is_file():
            return f"[error] FileReadTool: not a file — {raw_path!r}"

        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines(keepends=True)
            if len(lines) > line_count:
                truncated = "".join(lines[:line_count])
                return (
                    f"{truncated}\n[... truncated at {line_count}"
                    f" lines of {len(lines)} total]"
                )
            return text
        except PermissionError as e:
            return f"[permission denied] FileReadTool: {e}"
        except Exception as e:
            return f"[error] FileReadTool: {e}"
