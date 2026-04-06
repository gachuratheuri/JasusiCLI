"""Phase 8 CLI layer tests — minimum 30 tests."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

# --- OutputFormatter tests ---
from jasusi_cli.cli.output import OutputEvent, OutputFormat, OutputFormatter


def _event(
    event_type: str, content: str = "hello", metadata: dict[str, object] | None = None,
) -> OutputEvent:
    return OutputEvent(
        event_type=event_type,
        session_id="s1",
        content=content,
        metadata=metadata or {},
    )


def test_output_text_delta_writes_content() -> None:
    buf = io.StringIO()
    fmt = OutputFormatter(OutputFormat.TEXT, stream=buf)
    fmt.emit(_event("delta", "hello world"))
    assert "hello world" in buf.getvalue()


def test_output_text_error_writes_error_prefix() -> None:
    buf = io.StringIO()
    fmt = OutputFormatter(OutputFormat.TEXT, stream=buf)
    fmt.emit(_event("error", "something failed"))
    assert "[error]" in buf.getvalue()


def test_output_text_tool_call_writes_tool_label() -> None:
    buf = io.StringIO()
    fmt = OutputFormatter(OutputFormat.TEXT, stream=buf)
    ev = OutputEvent("tool_call", "s1", "", {"tool": "bash"})
    fmt.emit(ev)
    assert "tool: bash" in buf.getvalue()


def test_output_ndjson_writes_one_line_per_event() -> None:
    buf = io.StringIO()
    fmt = OutputFormatter(OutputFormat.NDJSON, stream=buf)
    fmt.emit(_event("delta", "a"))
    fmt.emit(_event("delta", "b"))
    lines = [raw_line for raw_line in buf.getvalue().splitlines() if raw_line.strip()]
    assert len(lines) == 2
    for line in lines:
        obj = json.loads(line)
        assert "event_type" in obj


def test_output_json_buffers_until_flush() -> None:
    buf = io.StringIO()
    fmt = OutputFormatter(OutputFormat.JSON, stream=buf)
    fmt.emit(_event("delta", "x"))
    assert buf.getvalue() == ""
    fmt.flush_json()
    data = json.loads(buf.getvalue())
    assert isinstance(data, list)
    assert data[0]["content"] == "x"


def test_output_json_flush_clears_buffer() -> None:
    buf = io.StringIO()
    fmt = OutputFormatter(OutputFormat.JSON, stream=buf)
    fmt.emit(_event("delta", "x"))
    fmt.flush_json()
    buf2 = io.StringIO()
    fmt._stream = buf2
    fmt.flush_json()
    assert buf2.getvalue().strip() == "[]"


def test_output_status_writes_in_text_mode() -> None:
    buf = io.StringIO()
    fmt = OutputFormatter(OutputFormat.TEXT, stream=buf)
    fmt.emit(_event("status", "Session started"))
    assert "Session started" in buf.getvalue()


# --- HistoryLog tests ---
from jasusi_cli.cli.history import HistoryEvent, HistoryLog


def test_history_append_returns_event(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    ev = log.append("sess-1", "First task", "do something")
    assert ev.seq == 1
    assert ev.session_id == "sess-1"
    assert ev.title == "First task"


def test_history_seq_increments(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    e1 = log.append("s", "title1", "d1")
    e2 = log.append("s", "title2", "d2")
    assert e2.seq > e1.seq


def test_history_read_all_returns_entries(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    log.append("s", "t1", "d1")
    log.append("s", "t2", "d2")
    entries = log.read_all()
    assert len(entries) == 2


def test_history_read_session_filters(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    log.append("sess-a", "t1", "d1")
    log.append("sess-b", "t2", "d2")
    entries = log.read_session("sess-a")
    assert len(entries) == 1
    assert entries[0].session_id == "sess-a"


def test_history_to_markdown_contains_titles(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    log.append("s", "My Important Task", "details")
    md = log.to_markdown()
    assert "My Important Task" in md
    assert "# History" in md


def test_history_to_markdown_empty(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    md = log.to_markdown()
    assert "No history entries" in md


def test_history_persists_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    log1 = HistoryLog(path)
    log1.append("s", "Persisted", "detail")
    log2 = HistoryLog(path)
    entries = log2.read_all()
    assert any(e.title == "Persisted" for e in entries)


def test_history_seq_continues_after_reload(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    log1 = HistoryLog(path)
    log1.append("s", "t1", "d1")
    log1.append("s", "t2", "d2")
    log2 = HistoryLog(path)
    e3 = log2.append("s", "t3", "d3")
    assert e3.seq == 3


def test_history_tags_stored_and_retrieved(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    log.append("s", "Tagged", "detail", tags=["turn", "important"])
    entries = log.read_all()
    assert "turn" in entries[0].tags


# --- CommandHandler tests ---
from jasusi_cli.cli.commands import CommandHandler, CommandResult


def _make_handler(tmp_path: Path) -> CommandHandler:
    return CommandHandler(
        session_id="test-sess", project="test-project", cwd=tmp_path,
    )


def test_command_not_slash_returns_unhandled(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("implement a parser")
    assert result.handled is False


def test_command_help_lists_all_commands(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/help")
    assert result.handled
    assert "/status" in result.output
    assert "/exit" in result.output


def test_command_version(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/version")
    assert result.handled
    assert "jasusi" in result.output


def test_command_status_shows_session(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/status")
    assert result.handled
    assert "test-sess" in result.output
    assert "test-project" in result.output


def test_command_cost_shows_dollars(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    h.update_stats(10_000, 5_000, 3)
    result = h.handle("/cost")
    assert result.handled
    assert "$" in result.output


def test_command_clear_sets_flag(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/clear")
    assert result.handled
    assert result.clear_history is True


def test_command_exit_sets_flag(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/exit")
    assert result.handled
    assert result.should_exit is True


def test_command_alias_q_exits(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/q")
    assert result.handled
    assert result.should_exit is True


def test_command_unknown_returns_error(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/notacommand")
    assert result.handled
    assert "Unknown command" in result.output


def test_command_init_creates_jasusi_md(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/init")
    assert result.handled
    assert (tmp_path / "JASUSI.md").exists()


def test_command_init_skips_if_exists(tmp_path: Path) -> None:
    (tmp_path / "JASUSI.md").write_text("existing")
    h = _make_handler(tmp_path)
    result = h.handle("/init")
    assert "already exists" in result.output
    assert (tmp_path / "JASUSI.md").read_text() == "existing"


def test_command_memory_shows_jasusi_md(tmp_path: Path) -> None:
    (tmp_path / "JASUSI.md").write_text("# My Rules\nAlways test.")
    h = _make_handler(tmp_path)
    result = h.handle("/memory")
    assert "Always test." in result.output


def test_command_permissions_shows_policy(tmp_path: Path) -> None:
    h = _make_handler(tmp_path)
    result = h.handle("/permissions")
    assert "ALLOW" in result.output
    assert "PROMPT" in result.output


# --- CLI entry point tests ---
from jasusi_cli.cli.entry import build_parser, run_cli


def test_entry_version_flag() -> None:
    code = run_cli(["--version"])
    assert code == 0


def test_entry_version_command() -> None:
    code = run_cli(["version"])
    assert code == 0


def test_entry_history_command() -> None:
    code = run_cli(["history"])
    assert code == 0


def test_parser_format_choices() -> None:
    parser = build_parser()
    args = parser.parse_args(["--format", "ndjson", "chat"])
    assert args.format == "ndjson"


def test_parser_simple_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["--simple", "chat"])
    assert args.simple is True
