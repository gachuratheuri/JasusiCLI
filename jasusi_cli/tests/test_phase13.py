"""Phase 13 — REPL wiring, BrailleSpinner, /compact, HistoryLog recording."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from jasusi_cli.cli.commands import COMMAND_REGISTRY, CommandHandler, CommandResult
from jasusi_cli.cli.history import HistoryLog
from jasusi_cli.cli.output import OutputFormat
from jasusi_cli.cli.repl import Repl
from jasusi_cli.cli.spinner import SPINNER_FRAMES, SPINNER_INTERVAL, BrailleSpinner
from jasusi_cli.core.runtime import ConversationRuntime
from jasusi_cli.integration.mock_clients import (
    MockApiClient,
    MockToolExecutor,
    MockTurn,
)
from jasusi_cli.integration.wiring import RuntimeConfig, RuntimeFactory


# ---------------------------------------------------------------------------
# BrailleSpinner tests
# ---------------------------------------------------------------------------


def test_spinner_frames_count() -> None:
    assert len(SPINNER_FRAMES) == 10


def test_spinner_frames_are_braille_unicode() -> None:
    for frame in SPINNER_FRAMES:
        assert len(frame) == 1
        assert 0x2800 <= ord(frame) <= 0x28FF


def test_spinner_interval_is_positive() -> None:
    assert SPINNER_INTERVAL > 0


def test_spinner_writes_to_stream() -> None:
    buf = io.StringIO()

    async def _run() -> None:
        async with BrailleSpinner("Test", stream=buf):
            await asyncio.sleep(0.01)

    asyncio.run(_run())


def test_spinner_clears_line_on_exit() -> None:
    buf = io.StringIO()

    async def _run() -> None:
        async with BrailleSpinner("Done", stream=buf):
            await asyncio.sleep(0.01)

    asyncio.run(_run())
    output = buf.getvalue()
    assert "\r" in output


def test_spinner_task_cancelled_on_exit() -> None:
    buf = io.StringIO()
    task_ref: list[asyncio.Task[None]] = []

    async def _run() -> None:
        spinner = BrailleSpinner("X", stream=buf)
        async with spinner:
            assert spinner._task is not None
            task_ref.append(spinner._task)
            await asyncio.sleep(0.01)
        assert task_ref[0].cancelled() or task_ref[0].done()

    asyncio.run(_run())


def test_spinner_can_be_used_multiple_times() -> None:
    buf = io.StringIO()

    async def _run() -> None:
        for _ in range(3):
            async with BrailleSpinner("Iter", stream=buf):
                await asyncio.sleep(0.005)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# CommandResult new fields tests
# ---------------------------------------------------------------------------


def test_command_result_has_compact_requested() -> None:
    result = CommandResult(handled=True, output="")
    assert hasattr(result, "compact_requested")
    assert result.compact_requested is False


def test_command_result_has_should_exit() -> None:
    result = CommandResult(handled=True, output="")
    assert hasattr(result, "should_exit")
    assert result.should_exit is False


def test_command_compact_sets_compact_requested(tmp_path: Path) -> None:
    handler = CommandHandler(session_id="s", project="p", cwd=tmp_path)
    result = handler.handle("/compact")
    assert result.handled
    assert result.compact_requested is True


def test_command_exit_sets_should_exit(tmp_path: Path) -> None:
    handler = CommandHandler(session_id="s", project="p", cwd=tmp_path)
    result = handler.handle("/exit")
    assert result.handled
    assert result.should_exit is True


def test_command_quit_alias_sets_should_exit(tmp_path: Path) -> None:
    handler = CommandHandler(session_id="s", project="p", cwd=tmp_path)
    result = handler.handle("/quit")
    assert result.handled
    assert result.should_exit is True


def test_command_q_alias_sets_should_exit(tmp_path: Path) -> None:
    handler = CommandHandler(session_id="s", project="p", cwd=tmp_path)
    result = handler.handle("/q")
    assert result.handled
    assert result.should_exit is True


def test_command_clear_sets_clear_history(tmp_path: Path) -> None:
    handler = CommandHandler(session_id="s", project="p", cwd=tmp_path)
    result = handler.handle("/clear")
    assert result.handled
    assert result.clear_history is True


# ---------------------------------------------------------------------------
# Repl construction tests (no stdin reads)
# ---------------------------------------------------------------------------


def test_repl_builds_without_error(tmp_path: Path) -> None:
    repl = Repl(cwd=tmp_path)
    assert repl is not None


def test_repl_accepts_output_format_string(tmp_path: Path) -> None:
    repl = Repl(output_format="ndjson", cwd=tmp_path)
    assert repl._fmt == OutputFormat.NDJSON


def test_repl_accepts_output_format_enum(tmp_path: Path) -> None:
    repl = Repl(output_format=OutputFormat.JSON, cwd=tmp_path)
    assert repl._fmt == OutputFormat.JSON


def test_repl_accepts_simple_mode(tmp_path: Path) -> None:
    repl = Repl(simple_mode=True, cwd=tmp_path)
    assert repl._simple_mode is True


def test_repl_accepts_session_id(tmp_path: Path) -> None:
    repl = Repl(session_id="existing-session", cwd=tmp_path)
    assert repl._session_id == "existing-session"


def test_repl_runtime_is_none_before_first_message(tmp_path: Path) -> None:
    repl = Repl(cwd=tmp_path)
    assert repl._runtime is None


def test_repl_ensure_runtime_builds_runtime(tmp_path: Path) -> None:
    repl = Repl(cwd=tmp_path)
    repl._ensure_runtime()
    assert repl._runtime is not None
    assert repl._session_id is not None


def test_repl_ensure_runtime_idempotent(tmp_path: Path) -> None:
    repl = Repl(cwd=tmp_path)
    repl._ensure_runtime()
    runtime_id = id(repl._runtime)
    repl._ensure_runtime()
    assert id(repl._runtime) == runtime_id


# ---------------------------------------------------------------------------
# Repl _process_turn with mock runtime (no stdin)
# ---------------------------------------------------------------------------


def test_repl_process_turn_records_history(tmp_path: Path) -> None:
    client = MockApiClient([MockTurn(text="hello from LLM")])
    executor = MockToolExecutor()
    repl = Repl(cwd=tmp_path)
    repl._ensure_runtime()
    assert repl._session_id is not None
    repl._runtime = ConversationRuntime(
        api_client=client,
        tool_executor=executor,
        session_id=repl._session_id,
        system_prompt="test",
    )
    asyncio.run(repl._process_turn("say hello"))
    entries = repl._history_log.read_all()
    assert len(entries) >= 1
    assert any("hello from LLM" in e.detail for e in entries)


def test_repl_process_turn_increments_turn_count(tmp_path: Path) -> None:
    client = MockApiClient([MockTurn(text="response 1"), MockTurn(text="response 2")])
    executor = MockToolExecutor()
    repl = Repl(cwd=tmp_path)
    repl._ensure_runtime()
    assert repl._session_id is not None
    repl._runtime = ConversationRuntime(
        api_client=client,
        tool_executor=executor,
        session_id=repl._session_id,
        system_prompt="",
    )
    asyncio.run(repl._process_turn("first"))
    asyncio.run(repl._process_turn("second"))
    assert repl._runtime.turn_count == 2


# ---------------------------------------------------------------------------
# HistoryLog integration — turn recording format
# ---------------------------------------------------------------------------


def test_history_log_turn_tags_include_turn(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    log.append("s1", "Turn 1", "user: hi\nassistant: hello\ntokens: 50", tags=["turn"])
    entries = log.read_all()
    assert any("turn" in e.tags for e in entries)


def test_history_log_interrupted_turn_has_interrupted_tag(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    log.append(
        "s1",
        "Turn 1",
        "user: go\nassistant: \ntokens: 10 [interrupted]",
        tags=["turn", "interrupted"],
    )
    entries = log.read_all()
    assert any("interrupted" in e.tags for e in entries)


def test_history_log_session_filter(tmp_path: Path) -> None:
    log = HistoryLog(tmp_path / "h.jsonl")
    log.append("s1", "Turn 1", "data1", tags=["turn"])
    log.append("s2", "Turn 1", "data2", tags=["turn"])
    s1_entries = log.read_session("s1")
    assert len(s1_entries) == 1
    assert s1_entries[0].session_id == "s1"


# ---------------------------------------------------------------------------
# RuntimeFactory integration
# ---------------------------------------------------------------------------


def test_runtime_factory_builds_with_defaults(tmp_path: Path) -> None:
    factory = RuntimeFactory(cwd=tmp_path)
    cfg = RuntimeConfig(session_id="test-sess", project="proj", cwd=tmp_path)
    runtime, worm, store = factory.build(config=cfg)
    assert runtime is not None
    assert runtime.turn_count == 0


def test_runtime_factory_respects_session_id(tmp_path: Path) -> None:
    factory = RuntimeFactory(cwd=tmp_path)
    cfg = RuntimeConfig(session_id="my-id-123", project="proj", cwd=tmp_path)
    runtime, _, _ = factory.build(config=cfg)
    assert runtime._session_id == "my-id-123"


def test_runtime_config_defaults() -> None:
    cfg = RuntimeConfig()
    assert cfg.simple_mode is False
    assert cfg.max_turns == 8
    assert cfg.compact_after_turns == 12
