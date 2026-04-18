"""Phase 11 — Bootstrap fast paths, TaskRunner, CLI entry, routing integration."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from jasusi_cli.bootstrap.graph import BootstrapContext, BootstrapGraph, BootstrapPhase
from jasusi_cli.cli.entry import build_parser, run_cli
from jasusi_cli.cli.task_runner import TaskRunner
from jasusi_cli.integration.mock_clients import MockApiClient, MockToolExecutor, MockTurn
from jasusi_cli.integration.wiring import RuntimeConfig, RuntimeFactory
from jasusi_cli.routing.scored_router import ScoredRouter


# ---------------------------------------------------------------------------
# BootstrapGraph — FastPath tests
# ---------------------------------------------------------------------------


def test_version_fast_path_returns_context() -> None:
    ctx = BootstrapGraph().run_version_fast_path()
    assert ctx.phase == BootstrapPhase.FAST_PATH_VERSION
    assert ctx.execution_mode == "version"


def test_version_fast_path_never_touches_settings() -> None:
    ctx = BootstrapGraph().run_version_fast_path()
    assert ctx.settings is None


def test_status_fast_path_returns_context(tmp_path: Path) -> None:
    ctx = BootstrapGraph(cwd=tmp_path).run_status_fast_path()
    assert ctx.phase == BootstrapPhase.FAST_PATH_STATUS
    assert ctx.execution_mode == "status"
    assert ctx.session_store is not None


def test_status_fast_path_zero_sessions_empty_dir(tmp_path: Path) -> None:
    ctx = BootstrapGraph(cwd=tmp_path).run_status_fast_path()
    assert ctx.session_store is not None
    sessions = ctx.session_store.list_sessions()
    assert sessions == []


def test_status_fast_path_sees_existing_session(tmp_path: Path) -> None:
    from jasusi_cli.memory.session_store import SessionStore

    store = SessionStore(base_dir=tmp_path / ".jasusi" / "sessions")
    store.create_session("abc123", project="myproject")
    ctx = BootstrapGraph(cwd=tmp_path).run_status_fast_path()
    assert ctx.session_store is not None
    sessions = ctx.session_store.list_sessions()
    assert any(s.session_id == "abc123" for s in sessions)


def test_bootstrap_version_constant() -> None:
    assert BootstrapGraph.VERSION == "0.14.0"


# ---------------------------------------------------------------------------
# BootstrapGraph — Full init path tests
# ---------------------------------------------------------------------------


def test_full_bootstrap_chat_mode(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(execution_mode="chat")
    )
    assert ctx.phase == BootstrapPhase.QUERY_ENGINE_SUBMIT
    assert ctx.settings is not None
    assert ctx.session_store is not None
    assert ctx.router is not None
    assert ctx.session_id is not None


def test_full_bootstrap_task_mode(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(
            execution_mode="task",
            task_input="implement a stack in Python",
        )
    )
    assert ctx.execution_mode == "task"
    assert ctx.task_input == "implement a stack in Python"


def test_full_bootstrap_warns_unknown_mode(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(execution_mode="invalid_mode")
    )
    assert ctx.execution_mode == "chat"


def test_full_bootstrap_simple_mode_flag(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(
            execution_mode="task", simple_mode=True
        )
    )
    assert ctx.simple_mode is True


def test_full_bootstrap_warning_handler_attaches(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(execution_mode="chat")
    )
    assert ctx.warnings_attached is True


def test_full_bootstrap_parallel_setup_loads_settings(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(execution_mode="chat")
    )
    assert ctx.settings is not None
    assert hasattr(ctx.settings, "providers")


def test_full_bootstrap_deferred_init_creates_router(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(execution_mode="chat")
    )
    assert ctx.router is not None
    assert isinstance(ctx.router, ScoredRouter)


def test_full_bootstrap_session_id_auto_generated(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(execution_mode="task")
    )
    assert ctx.session_id is not None
    assert len(ctx.session_id) >= 8


def test_full_bootstrap_session_id_preserved_when_provided(tmp_path: Path) -> None:
    ctx = asyncio.run(
        BootstrapGraph(cwd=tmp_path).run_full(
            execution_mode="task", session_id="fixed-session-id"
        )
    )
    assert ctx.session_id == "fixed-session-id"


# ---------------------------------------------------------------------------
# RuntimeFactory — ScoredRouter binding tests
# ---------------------------------------------------------------------------


def test_factory_routes_developer_task(tmp_path: Path) -> None:
    factory = RuntimeFactory(cwd=tmp_path)
    cfg = RuntimeConfig(
        session_id="route-dev",
        task_input="implement a linked list in Python",
        cwd=tmp_path,
    )
    runtime, _, _ = factory.build(
        config=cfg,
        api_client=MockApiClient([MockTurn(text="done")]),
        tool_executor=MockToolExecutor(),
    )
    assert runtime is not None


def test_factory_routes_researcher_task(tmp_path: Path) -> None:
    factory = RuntimeFactory(cwd=tmp_path)
    cfg = RuntimeConfig(
        session_id="route-res",
        task_input="what does the Python GIL do",
        cwd=tmp_path,
    )
    runtime, _, _ = factory.build(
        config=cfg,
        api_client=MockApiClient([MockTurn(text="done")]),
        tool_executor=MockToolExecutor(),
    )
    assert runtime is not None


def test_factory_empty_task_input_uses_developer_default(tmp_path: Path) -> None:
    factory = RuntimeFactory(cwd=tmp_path)
    cfg = RuntimeConfig(session_id="no-route", task_input="", cwd=tmp_path)
    runtime, _, _ = factory.build(
        config=cfg,
        api_client=MockApiClient([MockTurn(text="ok")]),
        tool_executor=MockToolExecutor(),
    )
    assert runtime is not None


# ---------------------------------------------------------------------------
# TaskRunner tests (mock API — no live network)
# ---------------------------------------------------------------------------


def test_task_runner_returns_zero_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jasusi_cli.integration import wiring as wiring_mod

    def _mock_build(
        self: object,
        config: object = None,
        api_client: object = None,
        tool_executor: object = None,
        prompter: object = None,
    ) -> tuple[object, object, object]:
        from jasusi_cli.core.runtime import ConversationRuntime
        from jasusi_cli.integration.worm_ledger import WormLedger
        from jasusi_cli.memory.session_store import SessionStore

        client = MockApiClient([MockTurn(text="Task complete.")])
        executor = MockToolExecutor()
        cfg_inner = config if isinstance(config, RuntimeConfig) else RuntimeConfig()
        store = SessionStore(base_dir=tmp_path / "sessions")
        store.create_session(cfg_inner.session_id, project=cfg_inner.project)
        rt: ConversationRuntime[object, object] = ConversationRuntime(
            api_client=client,
            tool_executor=executor,
            session_id=cfg_inner.session_id,
            system_prompt="",
        )
        return rt, WormLedger(persist_dir=str(tmp_path / "mem")), store

    monkeypatch.setattr(wiring_mod.RuntimeFactory, "build", _mock_build)
    runner = TaskRunner(cwd=tmp_path)
    result = runner.run("list all Python files")
    assert result == 0


def test_task_runner_returns_one_on_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jasusi_cli.integration import wiring as wiring_mod

    def _mock_build_raises(
        self: object,
        config: object = None,
        api_client: object = None,
        tool_executor: object = None,
        prompter: object = None,
    ) -> None:
        raise RuntimeError("Simulated provider failure")

    monkeypatch.setattr(wiring_mod.RuntimeFactory, "build", _mock_build_raises)
    runner = TaskRunner(cwd=tmp_path)
    result = runner.run("any task")
    assert result == 1


# ---------------------------------------------------------------------------
# CLI entry point — task and status subcommand tests
# ---------------------------------------------------------------------------


def test_cli_status_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run_cli(["status"]) == 0


def test_cli_task_empty_input_returns_nonzero() -> None:
    with pytest.raises(SystemExit) as exc:
        run_cli(["task"])
    assert exc.value.code in (1, 2)


def test_cli_version_flag_returns_zero() -> None:
    assert run_cli(["--version"]) == 0


def test_cli_version_subcommand_returns_zero() -> None:
    assert run_cli(["version"]) == 0


def test_cli_history_returns_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert run_cli(["history"]) == 0


def test_parser_task_subcommand_parses_input() -> None:
    parser = build_parser()
    args = parser.parse_args(["task", "fix", "the", "bug"])
    assert args.command == "task"
    assert args.input == ["fix", "the", "bug"]


def test_parser_simple_flag_default_false() -> None:
    parser = build_parser()
    args = parser.parse_args(["chat"])
    assert args.simple is False


def test_parser_simple_flag_set() -> None:
    parser = build_parser()
    args = parser.parse_args(["--simple", "chat"])
    assert args.simple is True


def test_parser_session_flag_default_none() -> None:
    parser = build_parser()
    args = parser.parse_args(["chat"])
    assert args.session is None


def test_parser_session_flag_set() -> None:
    parser = build_parser()
    args = parser.parse_args(["--session", "abc123", "chat"])
    assert args.session == "abc123"


# ---------------------------------------------------------------------------
# Live smoke test — skipped unless NEMOTRON_API_KEY is set
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.getenv("NEMOTRON_API_KEY"),
    reason="NEMOTRON_API_KEY not set — skipping live API smoke test",
)
def test_live_nemotron_single_turn(tmp_path: Path) -> None:
    """
    Live end-to-end smoke test against Nemotron API.
    Only runs when NEMOTRON_API_KEY is set in the environment.
    Verifies: authentication, SSE streaming, token counting, turn completion.
    """
    runner = TaskRunner(cwd=tmp_path)
    result = runner.run(
        task_input="Reply with exactly three words: hello from nemotron",
        output_format="text",
        simple_mode=True,
    )
    assert result == 0
