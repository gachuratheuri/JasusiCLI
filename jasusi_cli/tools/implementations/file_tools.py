"""FileReadTool, FileWriteTool, FileEditTool — atomic writes, line limits."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

READ_DEFAULT_LINES: int = 250
GLOB_MAX_RESULTS: int = 100


class FileReadTool:
    NAME: str = "file_read"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def execute(self, input_data: dict[str, object], session_id: str) -> str:
        rel_path = str(input_data.get("path", ""))
        offset = int(str(input_data.get("offset", 0)))
        limit = int(str(input_data.get("limit", READ_DEFAULT_LINES)))

        path = (self._cwd / rel_path).resolve()

        # Path traversal guard
        try:
            path.relative_to(self._cwd.resolve())
        except ValueError:
            return f"[error] Path traversal denied: {rel_path}"

        if not path.exists():
            return f"[error] File not found: {rel_path}"
        if not path.is_file():
            return f"[error] Not a file: {rel_path}"

        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            selected = lines[offset: offset + limit]
            logger.debug(
                "FileReadTool: session=%s path=%s lines=%d",
                session_id, rel_path, len(selected),
            )
            return "\n".join(selected)
        except OSError as e:
            return f"[error] Read failed: {e}"


class FileWriteTool:
    NAME: str = "file_write"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def execute(self, input_data: dict[str, object], session_id: str) -> str:
        rel_path = str(input_data.get("path", ""))
        content = str(input_data.get("content", ""))

        path = (self._cwd / rel_path).resolve()

        try:
            path.relative_to(self._cwd.resolve())
        except ValueError:
            return f"[error] Path traversal denied: {rel_path}"

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write: temp file → os.replace
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, path)
            logger.info(
                "FileWriteTool: session=%s wrote %d bytes to %s",
                session_id, len(content), rel_path,
            )
            return f"[ok] Wrote {len(content)} bytes to {rel_path}"
        except OSError as e:
            return f"[error] Write failed: {e}"


class FileEditTool:
    NAME: str = "file_edit"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def execute(self, input_data: dict[str, object], session_id: str) -> str:
        """
        Replace old_string with new_string in file.
        Returns unified diff-style output showing the change.
        """
        rel_path = str(input_data.get("path", ""))
        old_string = str(input_data.get("old_string", ""))
        new_string = str(input_data.get("new_string", ""))

        path = (self._cwd / rel_path).resolve()

        try:
            path.relative_to(self._cwd.resolve())
        except ValueError:
            return f"[error] Path traversal denied: {rel_path}"

        if not path.exists():
            return f"[error] File not found: {rel_path}"

        try:
            original = path.read_text(encoding="utf-8")
        except OSError as e:
            return f"[error] Read failed: {e}"

        if old_string not in original:
            return f"[error] old_string not found in {rel_path}"

        count = original.count(old_string)
        if count > 1:
            return (
                f"[error] old_string appears {count} times in {rel_path}. "
                f"Provide more context to make it unique."
            )

        modified = original.replace(old_string, new_string, 1)
        try:
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(modified, encoding="utf-8")
            os.replace(tmp, path)
            old_lines = len(old_string.splitlines())
            new_lines = len(new_string.splitlines())
            logger.info(
                "FileEditTool: session=%s edited %s (%d→%d lines)",
                session_id, rel_path, old_lines, new_lines,
            )
            return (
                f"[ok] Edited {rel_path}: "
                f"replaced {old_lines}-line block with {new_lines}-line block"
            )
        except OSError as e:
            return f"[error] Write failed: {e}"


class GlobSearchTool:
    NAME: str = "glob_search"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def execute(self, input_data: dict[str, object], session_id: str) -> str:
        pattern = str(input_data.get("pattern", "*"))
        results = sorted(self._cwd.rglob(pattern))[:GLOB_MAX_RESULTS]
        lines = [str(p.relative_to(self._cwd)) for p in results if p.is_file()]
        logger.debug(
            "GlobSearchTool: session=%s pattern=%s found=%d",
            session_id, pattern, len(lines),
        )
        return "\n".join(lines) if lines else "[no files matched]"


class GrepSearchTool:
    NAME: str = "grep_search"

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def execute(self, input_data: dict[str, object], session_id: str) -> str:
        pattern = str(input_data.get("pattern", ""))
        glob = str(input_data.get("glob", "**/*"))
        case_sensitive = bool(input_data.get("case_sensitive", True))

        if not pattern:
            return "[error] pattern is required"

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            compiled = re.compile(pattern, flags)
        except re.error as e:
            return f"[error] Invalid regex: {e}"

        matches: list[str] = []
        for path in sorted(self._cwd.rglob(glob))[:GLOB_MAX_RESULTS]:
            if not path.is_file():
                continue
            try:
                for i, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(),
                    1,
                ):
                    if compiled.search(line):
                        rel = path.relative_to(self._cwd)
                        matches.append(f"{rel}:{i}: {line.rstrip()}")
                        if len(matches) >= GLOB_MAX_RESULTS:
                            break
            except OSError:
                continue
            if len(matches) >= GLOB_MAX_RESULTS:
                break

        logger.debug(
            "GrepSearchTool: session=%s pattern=%s matches=%d",
            session_id, pattern, len(matches),
        )
        return "\n".join(matches) if matches else "[no matches]"
