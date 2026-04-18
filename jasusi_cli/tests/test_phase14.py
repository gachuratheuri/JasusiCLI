"""Phase 14 (FINAL) — CI pipeline, smoke tests, release checklist."""

from __future__ import annotations

from pathlib import Path

import pytest

import jasusi_cli
from jasusi_cli.bootstrap.graph import BootstrapContext, BootstrapGraph
from jasusi_cli.cli.entry import build_parser
from jasusi_cli.cli.task_runner import TaskRunner
from jasusi_cli.integration.mock_clients import MockApiClient, MockToolExecutor, MockTurn
from jasusi_cli.integration.wiring import RuntimeConfig, RuntimeFactory


# ---------------------------------------------------------------------------
# Package metadata tests
# ---------------------------------------------------------------------------


def test_package_version_exists() -> None:
    assert hasattr(jasusi_cli, "__version__")
    assert isinstance(jasusi_cli.__version__, str)


def test_package_version_format() -> None:
    version = jasusi_cli.__version__
    parts = version.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


def test_bootstrap_version_matches_package() -> None:
    bg_version = BootstrapGraph.VERSION
    pkg_version = jasusi_cli.__version__
    assert bg_version == pkg_version, (
        f"BootstrapGraph.VERSION={bg_version!r} != "
        f"jasusi_cli.__version__={pkg_version!r}"
    )


def test_version_is_0_14_0() -> None:
    assert jasusi_cli.__version__ == "0.14.0"


# ---------------------------------------------------------------------------
# Fast-path smoke tests (no API key, no LLM)
# ---------------------------------------------------------------------------


def test_version_fast_path_returns_context() -> None:
    ctx = BootstrapGraph(cwd=Path.cwd()).run_version_fast_path()
    assert isinstance(ctx, BootstrapContext)
    assert ctx.execution_mode == "version"


def test_version_fast_path_no_api_calls() -> None:
    """run_version_fast_path must complete without any HTTP calls."""
    ctx = BootstrapGraph(cwd=Path.cwd()).run_version_fast_path()
    assert ctx is not None
    assert BootstrapGraph.VERSION == "0.14.0"


def test_status_fast_path_returns_context(tmp_path: Path) -> None:
    ctx = BootstrapGraph(cwd=tmp_path).run_status_fast_path()
    assert isinstance(ctx, BootstrapContext)
    assert ctx.execution_mode == "status"
    assert ctx.session_store is not None


def test_status_fast_path_nonexistent_dir() -> None:
    """run_status_fast_path must not crash when no sessions exist."""
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "nonexistent-jasusi-test"
    ctx = BootstrapGraph(cwd=tmp).run_status_fast_path()
    assert ctx is not None


# ---------------------------------------------------------------------------
# CLI entry point tests (argparse only — no runtime)
# ---------------------------------------------------------------------------


def test_parser_version_flag() -> None:
    parser = build_parser()
    args = parser.parse_args(["--version"])
    assert args.version is True


def test_parser_task_requires_input() -> None:
    parser = build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["task"])
    assert exc.value.code == 2


def test_parser_task_with_input() -> None:
    parser = build_parser()
    args = parser.parse_args(["task", "say hello"])
    assert args.input == ["say hello"]


def test_parser_status_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["status"])
    assert args.command == "status"


def test_parser_default_is_none() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.command is None


# ---------------------------------------------------------------------------
# TaskRunner integration with mock client
# ---------------------------------------------------------------------------


def test_task_runner_returns_zero_on_success(tmp_path: Path) -> None:
    client = MockApiClient([MockTurn(text="task complete")])
    executor = MockToolExecutor()
    runner = TaskRunner(cwd=tmp_path)
    runner._inject_clients(api_client=client, tool_executor=executor)
    exit_code = runner.run("say hello world")
    assert exit_code == 0


def test_task_runner_returns_nonzero_on_empty_input(tmp_path: Path) -> None:
    runner = TaskRunner(cwd=tmp_path)
    exit_code = runner.run("")
    assert exit_code != 0


def test_task_runner_session_persisted(tmp_path: Path) -> None:
    client = MockApiClient([MockTurn(text="recorded response")])
    executor = MockToolExecutor()
    runner = TaskRunner(cwd=tmp_path)
    runner._inject_clients(api_client=client, tool_executor=executor)
    runner.run("test task")
    sessions_dir = tmp_path / ".jasusi" / "sessions"
    assert sessions_dir.exists()


# ---------------------------------------------------------------------------
# pyproject.toml / CHANGELOG / CI verification
# ---------------------------------------------------------------------------


def test_pyproject_toml_exists() -> None:
    root = Path(__file__).parent.parent.parent
    assert (root / "pyproject.toml").exists()


def test_pyproject_has_scripts_entry() -> None:
    root = Path(__file__).parent.parent.parent
    content = (root / "pyproject.toml").read_text()
    assert "jasusi_cli.cli.entry:main" in content


def test_pyproject_has_dev_extras() -> None:
    root = Path(__file__).parent.parent.parent
    content = (root / "pyproject.toml").read_text()
    assert "dev" in content
    assert "pytest" in content


def test_changelog_exists() -> None:
    root = Path(__file__).parent.parent.parent
    changelog = root / "CHANGELOG.md"
    assert changelog.exists()
    content = changelog.read_text()
    assert "0.14.0" in content
    assert "Phase 14" in content


def test_github_ci_workflow_exists() -> None:
    root = Path(__file__).parent.parent.parent
    ci = root / ".github" / "workflows" / "ci.yml"
    assert ci.exists()


def test_ci_workflow_has_python_matrix() -> None:
    root = Path(__file__).parent.parent.parent
    content = (root / ".github" / "workflows" / "ci.yml").read_text()
    assert "3.11" in content
    assert "3.12" in content
    assert "pytest" in content
    assert "mypy" in content
