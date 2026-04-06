"""Phase 9 Integration layer tests — minimum 30 tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from jasusi_cli.api.client import StreamChunk
from jasusi_cli.core.runtime import (
    ConversationRuntime,
    Message,
    QueryEngineConfig,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from jasusi_cli.integration.mock_clients import (
    MockApiClient,
    MockToolExecutor,
    MockTurn,
)
from jasusi_cli.integration.worm_ledger import WormLedger, _sanitise
from jasusi_cli.integration.wiring import RuntimeConfig, RuntimeFactory


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_runtime(
    turns: list[MockTurn] | None = None,
    tool_responses: dict[str, str] | None = None,
    session_id: str = "test-sess",
    max_turns: int = 8,
    compact_after_turns: int = 12,
) -> tuple[ConversationRuntime[object, object], MockApiClient, MockToolExecutor]:
    client = MockApiClient(turns=turns)
    executor = MockToolExecutor(responses=tool_responses)
    runtime: ConversationRuntime[object, object] = ConversationRuntime(
        api_client=client,
        tool_executor=executor,
        session_id=session_id,
        system_prompt="You are a helpful agent.",
        max_turns=max_turns,
        compact_after_turns=compact_after_turns,
    )
    return runtime, client, executor


async def _collect(
    runtime: ConversationRuntime[object, object], text: str,
) -> str:
    result = ""
    stream = await runtime.submit(text)
    async for chunk in stream:
        result += chunk.delta
    return result


# ---------------------------------------------------------------------------
# MockApiClient tests
# ---------------------------------------------------------------------------


def test_mock_client_text_turn() -> None:
    async def _run() -> str:
        client = MockApiClient([MockTurn(text="Hello world")])
        chunks: list[str] = []
        stream = await client.complete([], [], "sys")
        async for chunk in stream:
            chunks.append(chunk.delta)
        return "".join(chunks)

    result = asyncio.run(_run())
    assert "Hello" in result


def test_mock_client_tool_call_turn() -> None:
    async def _run() -> list[StreamChunk]:
        client = MockApiClient([
            MockTurn(tool_name="bash", tool_input={"command": "ls"}),
        ])
        stream = await client.complete([], [], "sys")
        return [c async for c in stream]

    chunks = asyncio.run(_run())
    assert any(c.is_tool_call for c in chunks)
    assert any(c.tool_name == "bash" for c in chunks)


def test_mock_client_default_response_when_queue_empty() -> None:
    async def _run() -> str:
        client = MockApiClient()
        chunks: list[str] = []
        stream = await client.complete([], [], "sys")
        async for chunk in stream:
            chunks.append(chunk.delta)
        return "".join(chunks)

    result = asyncio.run(_run())
    assert "Task complete" in result


def test_mock_client_records_calls() -> None:
    async def _run() -> int:
        client = MockApiClient([MockTurn(text="done")])
        stream = await client.complete([], [], "sys")
        async for _ in stream:
            pass
        return client.call_count

    assert asyncio.run(_run()) == 1


def test_mock_client_push_adds_turn() -> None:
    async def _run() -> str:
        client = MockApiClient()
        client.push(MockTurn(text="pushed response"))
        chunks: list[str] = []
        stream = await client.complete([], [], "sys")
        async for chunk in stream:
            chunks.append(chunk.delta)
        return "".join(chunks)

    assert "pushed" in asyncio.run(_run())


def test_mock_client_reset_clears_state() -> None:
    async def _run() -> None:
        client = MockApiClient([MockTurn(text="x")])
        stream = await client.complete([], [], "sys")
        async for _ in stream:
            pass
        client.reset()
        assert client.call_count == 0

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# MockToolExecutor tests
# ---------------------------------------------------------------------------


def test_mock_executor_records_calls() -> None:
    result = asyncio.run(
        MockToolExecutor().execute("bash", b'{"command":"ls"}', "sess"),
    )
    assert result is not None


def test_mock_executor_was_called() -> None:
    executor = MockToolExecutor()
    asyncio.run(executor.execute("file_read", b'{"path":"x"}', "sess"))
    assert executor.was_called("file_read")
    assert not executor.was_called("bash")


def test_mock_executor_custom_response() -> None:
    executor = MockToolExecutor(responses={"bash": "custom output"})
    result = asyncio.run(executor.execute("bash", b"{}", "sess"))
    assert result == "custom output"


def test_mock_executor_default_response() -> None:
    executor = MockToolExecutor()
    result = asyncio.run(executor.execute("glob_search", b"{}", "sess"))
    assert "glob_search" in result


def test_mock_executor_visible_schemas() -> None:
    schemas = MockToolExecutor().visible_schemas()
    assert len(schemas) >= 1
    assert schemas[0]["name"] == "bash"


# ---------------------------------------------------------------------------
# WormLedger tests
# ---------------------------------------------------------------------------


def test_worm_ledger_upsert_and_count(tmp_path: Path) -> None:
    ledger = WormLedger(persist_dir=str(tmp_path / "mem"))
    ledger.upsert("Fix the async bug", session_id="s1")
    assert ledger.count() >= 1


def test_worm_ledger_query_fallback(tmp_path: Path) -> None:
    ledger = WormLedger(persist_dir=str(tmp_path / "mem"))
    ledger.upsert("Implement session store with JSONL", session_id="s1")
    ledger.upsert("Fix the router timeout bug", session_id="s1")
    results = ledger.query("session store")
    assert any("session store" in r.text.lower() for r in results)


def test_worm_ledger_delete_session(tmp_path: Path) -> None:
    ledger = WormLedger(persist_dir=str(tmp_path / "mem"))
    ledger.upsert("entry A", session_id="sess-a")
    ledger.upsert("entry B", session_id="sess-b")
    ledger.delete_session("sess-a")
    results = ledger.query("entry")
    assert all(r.session_id != "sess-a" for r in results)


def test_worm_ledger_sanitise_strips_api_key() -> None:
    dirty = "API key is sk-ant-abcdefghijklmnopqrst123456 use it wisely"
    clean = _sanitise(dirty)
    assert "sk-ant" not in clean
    assert "[REDACTED]" in clean


def test_worm_ledger_sanitise_strips_jwt() -> None:
    dirty = (
        "token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxw"
    )
    clean = _sanitise(dirty)
    assert "eyJ" not in clean


def test_worm_ledger_sanitise_passthrough_safe_text() -> None:
    safe = "Implement the session compaction algorithm using preserve_recent=4"
    assert _sanitise(safe) == safe


def test_worm_ledger_flush_session_to_memory(tmp_path: Path) -> None:
    ledger = WormLedger(persist_dir=str(tmp_path / "mem"))
    doc_id = ledger.flush_session_to_memory(
        session_id="sess-flush",
        decisions=["Use JSONL for transcripts", "Add retry logic"],
        files_modified=["jasusi_cli/api/client.py"],
        pending_work="Wire up Phase 9 integration",
    )
    assert len(doc_id) > 0
    results = ledger.query("Decisions Made")
    assert any("sess-flush" in r.session_id for r in results)


def test_worm_ledger_upsert_idempotent(tmp_path: Path) -> None:
    ledger = WormLedger(persist_dir=str(tmp_path / "mem"))
    id1 = ledger.upsert("identical content", session_id="s1")
    id2 = ledger.upsert("identical content", session_id="s1")
    assert id1 == id2
    assert ledger.count() == 1


# ---------------------------------------------------------------------------
# ConversationRuntime tests
# ---------------------------------------------------------------------------


def test_runtime_text_only_turn() -> None:
    runtime, _, _ = _make_runtime([MockTurn(text="Hello!")])
    result = asyncio.run(_collect(runtime, "say hi"))
    assert "Hello" in result
    assert runtime.turn_count == 1


def test_runtime_increments_turn_count() -> None:
    runtime, _, _ = _make_runtime([
        MockTurn(text="first"), MockTurn(text="second"),
    ])
    asyncio.run(_collect(runtime, "turn 1"))
    asyncio.run(_collect(runtime, "turn 2"))
    assert runtime.turn_count == 2


def test_runtime_tool_call_then_text() -> None:
    runtime, client, executor = _make_runtime(
        turns=[
            MockTurn(
                tool_name="bash",
                tool_input={"command": "ls"},
                tool_use_id="u1",
            ),
            MockTurn(text="Files listed successfully."),
        ],
        tool_responses={"bash": "main.py\ntest.py"},
    )
    result = asyncio.run(_collect(runtime, "list files"))
    assert "Files listed" in result
    assert executor.was_called("bash")
    assert client.call_count == 2


def test_runtime_tool_result_appended_to_history() -> None:
    runtime, _, executor = _make_runtime(
        turns=[
            MockTurn(
                tool_name="file_read",
                tool_input={"path": "x"},
                tool_use_id="u1",
            ),
            MockTurn(text="Read the file."),
        ],
    )
    asyncio.run(_collect(runtime, "read file"))
    assert executor.was_called("file_read")
    assert len(runtime._history) >= 3


def test_runtime_max_turns_enforced() -> None:
    runtime, _, _ = _make_runtime(
        turns=[MockTurn(text=f"response {i}") for i in range(20)],
        max_turns=2,
    )
    asyncio.run(_collect(runtime, "turn 1"))
    asyncio.run(_collect(runtime, "turn 2"))
    result = asyncio.run(_collect(runtime, "turn 3"))
    assert "turn limit" in result
    assert runtime.turn_count == 2


def test_runtime_clear_history_resets_turns() -> None:
    runtime, _, _ = _make_runtime([MockTurn(text="hi"), MockTurn(text="hi")])
    asyncio.run(_collect(runtime, "first"))
    runtime.clear_history()
    assert runtime.turn_count == 0
    assert len(runtime._history) == 0


def test_runtime_compaction_fires_after_threshold() -> None:
    runtime, _, _ = _make_runtime(
        turns=[MockTurn(text=f"response {i}") for i in range(20)],
        compact_after_turns=3,
    )
    for i in range(3):
        asyncio.run(_collect(runtime, f"turn {i}"))
    assert runtime.compaction_count >= 1


def test_runtime_compaction_preserves_recent_4() -> None:
    runtime, _, _ = _make_runtime(
        turns=[MockTurn(text=f"r{i}") for i in range(20)],
        compact_after_turns=3,
    )
    for i in range(3):
        asyncio.run(_collect(runtime, f"q{i}"))
    assert len(runtime._history) <= 6


def test_runtime_total_tokens_accumulates() -> None:
    runtime, _, _ = _make_runtime([
        MockTurn(text="ok", input_tokens=100, output_tokens=50),
    ])
    asyncio.run(_collect(runtime, "go"))
    assert runtime.total_tokens > 0


# ---------------------------------------------------------------------------
# RuntimeFactory tests
# ---------------------------------------------------------------------------


def test_factory_builds_runtime_with_mocks(tmp_path: Path) -> None:
    factory = RuntimeFactory(cwd=tmp_path)
    client = MockApiClient([MockTurn(text="wired!")])
    executor = MockToolExecutor()
    cfg = RuntimeConfig(
        session_id="wired-sess", project="test", cwd=tmp_path,
    )
    runtime, worm, store = factory.build(
        config=cfg, api_client=client, tool_executor=executor,
    )
    assert runtime is not None
    assert worm is not None
    assert store is not None


def test_factory_creates_session_in_store(tmp_path: Path) -> None:
    factory = RuntimeFactory(cwd=tmp_path)
    cfg = RuntimeConfig(
        session_id="new-sess", project="myproject", cwd=tmp_path,
    )
    _, _, store = factory.build(
        config=cfg,
        api_client=MockApiClient(),
        tool_executor=MockToolExecutor(),
    )
    session = store.get_session("new-sess")
    assert session is not None
    assert session.project == "myproject"


def test_factory_end_to_end_text_turn(tmp_path: Path) -> None:
    factory = RuntimeFactory(cwd=tmp_path)
    client = MockApiClient([MockTurn(text="end to end works")])
    executor = MockToolExecutor()
    cfg = RuntimeConfig(session_id="e2e-sess", cwd=tmp_path)
    runtime, _, _ = factory.build(
        config=cfg, api_client=client, tool_executor=executor,
    )
    result = asyncio.run(_collect(runtime, "test task"))
    assert "end to end" in result
