"""Phase 7 tool executor layer tests — minimum 30 tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

# --- Schema tests ---
from jasusi_cli.tools.schema import ToolParameter, ToolSpec


def test_tool_spec_to_json_schema_structure() -> None:
    spec = ToolSpec(
        name="test_tool",
        description="A test tool",
        parameters=[
            ToolParameter("cmd", "string", "Command to run"),
            ToolParameter("timeout", "integer", "Timeout", required=False),
        ],
    )
    schema = spec.to_json_schema()
    assert schema["name"] == "test_tool"
    assert "cmd" in schema["input_schema"]["properties"]
    assert "cmd" in schema["input_schema"]["required"]
    assert "timeout" not in schema["input_schema"]["required"]


def test_tool_spec_enum_values() -> None:
    spec = ToolSpec(
        name="mode_tool",
        description="Mode selector",
        parameters=[
            ToolParameter("mode", "string", "Mode", enum_values=["fast", "slow"]),
        ],
    )
    schema = spec.to_json_schema()
    assert schema["input_schema"]["properties"]["mode"]["enum"] == ["fast", "slow"]


# --- Registry tests ---
from jasusi_cli.tools.registry import ToolRegistry, ValidationError


def _make_registry(simple: bool = False) -> ToolRegistry:
    r = ToolRegistry(simple_mode=simple)
    r.register(ToolSpec("bash", "Run shell", [ToolParameter("command", "string", "cmd")]))
    r.register(ToolSpec("file_read", "Read file", [ToolParameter("path", "string", "path")]))
    r.register(ToolSpec("file_edit", "Edit file", [
        ToolParameter("path", "string", "p"),
        ToolParameter("old_string", "string", "old"),
        ToolParameter("new_string", "string", "new"),
    ]))
    r.register(ToolSpec("risky_tool", "Risky", [ToolParameter("x", "string", "x")]))
    return r


def test_registry_validates_valid_input() -> None:
    r = _make_registry()
    result = r.validate("bash", b'{"command": "ls"}')
    assert result["command"] == "ls"


def test_registry_rejects_unknown_tool() -> None:
    r = _make_registry()
    with pytest.raises(ValidationError, match="Unknown tool"):
        r.validate("nonexistent", b'{"x": "y"}')


def test_registry_rejects_invalid_json() -> None:
    r = _make_registry()
    with pytest.raises(ValidationError, match="Invalid JSON"):
        r.validate("bash", b"not-json")


def test_registry_rejects_missing_required_param() -> None:
    r = _make_registry()
    with pytest.raises(ValidationError, match="missing required"):
        r.validate("bash", b"{}")


def test_registry_simple_mode_blocks_risky_tool() -> None:
    r = _make_registry(simple=True)
    with pytest.raises(ValidationError, match="not available in simple mode"):
        r.validate("risky_tool", b'{"x": "y"}')


def test_registry_simple_mode_allows_bash() -> None:
    r = _make_registry(simple=True)
    result = r.validate("bash", b'{"command": "echo hi"}')
    assert result["command"] == "echo hi"


def test_registry_visible_specs_cap_at_15() -> None:
    r = ToolRegistry()
    for i in range(20):
        r.register(ToolSpec(f"tool_{i}", f"Tool {i}", []))
    assert len(r.visible_specs()) <= 15


def test_registry_simple_mode_visible_specs_exactly_3() -> None:
    r = _make_registry(simple=True)
    specs = r.visible_specs()
    names = {s.name for s in specs}
    assert names == {"bash", "file_read", "file_edit"}


# --- Permission tests ---
from jasusi_cli.tools.permissions import (
    AutoAllowPrompter,
    AutoDenyPrompter,
    PermissionMode,
    PermissionPolicy,
)


def test_permission_allow_safe_tool() -> None:
    policy = PermissionPolicy(prompter=AutoAllowPrompter())
    assert policy.check("file_read", "read main.py") is True


def test_permission_deny_with_auto_deny_prompter() -> None:
    policy = PermissionPolicy(prompter=AutoDenyPrompter())
    # bash is PROMPT mode by default — AutoDenyPrompter returns False
    assert policy.check("bash", "rm -rf /") is False


def test_permission_override_deny() -> None:
    policy = PermissionPolicy(
        prompter=AutoAllowPrompter(),
        overrides={"file_read": PermissionMode.DENY},
    )
    assert policy.check("file_read", "read secret") is False


def test_permission_get_returns_correct_mode() -> None:
    policy = PermissionPolicy()
    assert policy.get("file_read") == PermissionMode.ALLOW
    assert policy.get("bash") == PermissionMode.PROMPT


def test_permission_set_updates_policy() -> None:
    policy = PermissionPolicy(prompter=AutoAllowPrompter())
    policy.set("bash", PermissionMode.ALLOW)
    assert policy.check("bash", "cargo build") is True


# --- BashTool tests ---
from jasusi_cli.tools.implementations.bash_tool import BashTool


def test_bash_tool_echo(tmp_path: Path) -> None:
    tool = BashTool(cwd=tmp_path)
    result = tool.execute({"command": "python -c \"print('hello')\" "}, "sess-1")
    assert "hello" in result


def test_bash_tool_empty_command(tmp_path: Path) -> None:
    tool = BashTool(cwd=tmp_path)
    result = tool.execute({"command": ""}, "sess-1")
    assert "Empty command" in result


def test_bash_tool_nonexistent_binary(tmp_path: Path) -> None:
    tool = BashTool(cwd=tmp_path)
    result = tool.execute({"command": "nonexistent_binary_xyz_123"}, "sess-1")
    assert "[error]" in result


# --- FileReadTool tests ---
from jasusi_cli.tools.implementations.file_tools import (
    FileEditTool,
    FileReadTool,
    FileWriteTool,
    GlobSearchTool,
    GrepSearchTool,
)


def test_file_read_reads_content(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("line1\nline2\nline3")
    tool = FileReadTool(cwd=tmp_path)
    result = tool.execute({"path": "hello.txt"}, "sess-1")
    assert "line1" in result
    assert "line2" in result


def test_file_read_path_traversal_denied(tmp_path: Path) -> None:
    tool = FileReadTool(cwd=tmp_path)
    result = tool.execute({"path": "../../etc/passwd"}, "sess-1")
    assert "[error]" in result
    assert "traversal" in result


def test_file_read_missing_file(tmp_path: Path) -> None:
    tool = FileReadTool(cwd=tmp_path)
    result = tool.execute({"path": "ghost.txt"}, "sess-1")
    assert "[error]" in result


# --- FileWriteTool / FileEditTool tests ---
def test_file_write_creates_file(tmp_path: Path) -> None:
    tool = FileWriteTool(cwd=tmp_path)
    result = tool.execute({"path": "out.txt", "content": "hello world"}, "sess-1")
    assert "[ok]" in result
    assert (tmp_path / "out.txt").read_text() == "hello world"


def test_file_edit_replaces_string(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("def foo():\n    pass\n")
    tool = FileEditTool(cwd=tmp_path)
    result = tool.execute({
        "path": "src.py",
        "old_string": "    pass",
        "new_string": "    return 42",
    }, "sess-1")
    assert "[ok]" in result
    assert "return 42" in (tmp_path / "src.py").read_text()


def test_file_edit_fails_when_old_string_missing(tmp_path: Path) -> None:
    (tmp_path / "src.py").write_text("def foo(): pass\n")
    tool = FileEditTool(cwd=tmp_path)
    result = tool.execute({
        "path": "src.py",
        "old_string": "NONEXISTENT",
        "new_string": "replacement",
    }, "sess-1")
    assert "[error]" in result


# --- GlobSearchTool / GrepSearchTool tests ---
def test_glob_search_finds_files(tmp_path: Path) -> None:
    (tmp_path / "a.rs").write_text("fn main() {}")
    (tmp_path / "b.rs").write_text("fn lib() {}")
    tool = GlobSearchTool(cwd=tmp_path)
    result = tool.execute({"pattern": "*.rs"}, "sess-1")
    assert "a.rs" in result
    assert "b.rs" in result


def test_grep_search_finds_pattern(tmp_path: Path) -> None:
    (tmp_path / "main.rs").write_text('fn main() {\n    println!("hello");\n}\n')
    tool = GrepSearchTool(cwd=tmp_path)
    result = tool.execute({"pattern": "println", "glob": "*.rs"}, "sess-1")
    assert "main.rs" in result
    assert "println" in result


# --- ToolExecutor integration tests ---
from jasusi_cli.tools.tool_executor import ToolExecutor


def test_executor_dispatches_file_read(tmp_path: Path) -> None:
    (tmp_path / "readme.md").write_text("# Hello\nWorld\n")
    executor = ToolExecutor(cwd=tmp_path)
    result = asyncio.run(
        executor.execute("file_read", b'{"path": "readme.md"}', "sess-exec")
    )
    assert "Hello" in result


def test_executor_permission_denied_returns_message(tmp_path: Path) -> None:
    executor = ToolExecutor(
        cwd=tmp_path,
        prompter=AutoDenyPrompter(),
    )
    result = asyncio.run(
        executor.execute("bash", b'{"command": "echo test"}', "sess-deny")
    )
    assert "permission_denied" in result


def test_executor_validation_error_returns_message(tmp_path: Path) -> None:
    executor = ToolExecutor(cwd=tmp_path)
    result = asyncio.run(
        executor.execute("bash", b"{}", "sess-bad")
    )
    assert "validation_error" in result


def test_executor_visible_schemas_count(tmp_path: Path) -> None:
    executor = ToolExecutor(cwd=tmp_path)
    schemas = executor.visible_schemas()
    assert len(schemas) <= 15
    assert all("name" in s for s in schemas)


def test_executor_simple_mode_rejects_risky(tmp_path: Path) -> None:
    executor = ToolExecutor(cwd=tmp_path, simple_mode=True)
    result = asyncio.run(
        executor.execute("file_write", b'{"path":"x.txt","content":"x"}', "sess-s")
    )
    assert "simple mode" in result or "not available" in result
