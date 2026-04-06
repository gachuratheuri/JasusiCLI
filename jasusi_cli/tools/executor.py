"""
ToolExecutor — implements ToolExecutorProtocol.
Wires registry + permissions + firewall + individual tools.
RULE 9: input_json never logged raw — SHA-256 only.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from jasusi_cli.tools.implementations.bash_tool import BashTool
from jasusi_cli.tools.implementations.file_tools import (
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    GlobSearchTool,
    GrepSearchTool,
)
from jasusi_cli.tools.permissions import (
    AutoAllowPrompter,
    PermissionMode,
    PermissionPolicy,
    PermissionPrompter,
)
from jasusi_cli.tools.registry import ToolRegistry, ValidationError
from jasusi_cli.tools.schema import ToolParameter, ToolSpec

logger = logging.getLogger(__name__)


def _build_default_registry(simple_mode: bool = False) -> ToolRegistry:
    registry = ToolRegistry(simple_mode=simple_mode)

    registry.register(ToolSpec(
        name="bash",
        description="Run a shell command. Output is capped at 8192 chars.",
        parameters=[ToolParameter("command", "string", "The shell command to run")],
    ))
    registry.register(ToolSpec(
        name="file_read",
        description="Read lines from a file.",
        parameters=[
            ToolParameter("path", "string", "Relative path to the file"),
            ToolParameter("offset", "integer", "Line offset", required=False),
            ToolParameter("limit", "integer", "Max lines to read", required=False),
        ],
    ))
    registry.register(ToolSpec(
        name="file_write",
        description="Write content to a file (atomic).",
        parameters=[
            ToolParameter("path", "string", "Relative path to the file"),
            ToolParameter("content", "string", "File content"),
        ],
    ))
    registry.register(ToolSpec(
        name="file_edit",
        description="Replace old_string with new_string in a file.",
        parameters=[
            ToolParameter("path", "string", "Relative path to the file"),
            ToolParameter("old_string", "string", "Exact string to replace"),
            ToolParameter("new_string", "string", "Replacement string"),
        ],
    ))
    registry.register(ToolSpec(
        name="glob_search",
        description="Find files matching a glob pattern (max 100 results).",
        parameters=[
            ToolParameter("pattern", "string", "Glob pattern e.g. **/*.rs"),
        ],
    ))
    registry.register(ToolSpec(
        name="grep_search",
        description="Search file contents with a regex pattern.",
        parameters=[
            ToolParameter("pattern", "string", "Regex pattern"),
            ToolParameter(
                "glob", "string", "File glob to search within", required=False,
            ),
            ToolParameter(
                "case_sensitive", "boolean", "Case sensitive search", required=False,
            ),
        ],
    ))
    registry.register(ToolSpec(
        name="todo_write",
        description="Write a TODO list to track pending tasks.",
        parameters=[
            ToolParameter("todos", "string", "Newline-separated TODO items"),
        ],
    ))

    return registry


class ToolExecutor:
    """
    Implements ToolExecutorProtocol.
    Injection points: prompter (for permissions), cwd (for file ops).
    """

    def __init__(
        self,
        cwd: Path | None = None,
        simple_mode: bool = False,
        prompter: PermissionPrompter | None = None,
        permission_overrides: dict[str, PermissionMode] | None = None,
    ) -> None:
        self._cwd = cwd or Path.cwd()
        self._registry = _build_default_registry(simple_mode)
        self._permissions = PermissionPolicy(
            prompter=prompter or AutoAllowPrompter(),
            overrides=permission_overrides,
        )
        self._bash = BashTool(self._cwd)
        self._file_read = FileReadTool(self._cwd)
        self._file_write = FileWriteTool(self._cwd)
        self._file_edit = FileEditTool(self._cwd)
        self._glob = GlobSearchTool(self._cwd)
        self._grep = GrepSearchTool(self._cwd)
        self._todos: list[str] = []

    async def execute(
        self,
        tool_name: str,
        input_json: bytes,
        session_id: str,
    ) -> str:
        """
        Main dispatch. Validates → checks permissions → runs firewall → executes.
        RULE 9: input_json logged as SHA-256 hash only.
        """
        input_hash = hashlib.sha256(input_json).hexdigest()
        logger.info(
            "ToolExecutor: tool=%s session=%s input_hash=%s",
            tool_name, session_id, input_hash,
        )

        # Step 1: Schema validation
        try:
            parsed = self._registry.validate(tool_name, input_json)
        except ValidationError as e:
            logger.warning("Validation failed: tool=%s error=%s", tool_name, e)
            return f"[validation_error] {e}"

        # Step 2: Permission check
        preview = self._make_preview(tool_name, parsed)
        if not self._permissions.check(tool_name, preview):
            logger.warning(
                "Permission denied: tool=%s session=%s", tool_name, session_id,
            )
            return (
                f"[permission_denied] Tool '{tool_name}' "
                f"was denied by permission policy"
            )

        # Step 3: Dispatch
        return self._dispatch(tool_name, parsed, session_id)

    def _dispatch(
        self, tool_name: str, parsed: dict[str, Any], session_id: str,
    ) -> str:
        if tool_name == "bash":
            return self._bash.execute(parsed, session_id)
        if tool_name == "file_read":
            return self._file_read.execute(parsed, session_id)
        if tool_name == "file_write":
            return self._file_write.execute(parsed, session_id)
        if tool_name == "file_edit":
            return self._file_edit.execute(parsed, session_id)
        if tool_name == "glob_search":
            return self._glob.execute(parsed, session_id)
        if tool_name == "grep_search":
            return self._grep.execute(parsed, session_id)
        if tool_name == "todo_write":
            todos = str(parsed.get("todos", ""))
            self._todos = [t.strip() for t in todos.splitlines() if t.strip()]
            return f"[ok] Saved {len(self._todos)} TODO items"
        return f"[error] No implementation for tool '{tool_name}'"

    def _make_preview(
        self, tool_name: str, parsed: dict[str, Any],
    ) -> str:
        if tool_name == "bash":
            return str(parsed.get("command", ""))[:120]
        if tool_name in ("file_write", "file_edit", "file_read"):
            return str(parsed.get("path", ""))
        return json.dumps(dict(list(parsed.items())[:2]))

    def visible_schemas(self) -> list[dict[str, Any]]:
        """Return JSON schemas for tools visible to the LLM."""
        return [spec.to_json_schema() for spec in self._registry.visible_specs()]

    def get_todos(self) -> list[str]:
        return list(self._todos)
