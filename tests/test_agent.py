"""Tests for JasusiCLI Agent autonomous coding assistant."""

from __future__ import annotations

import io
import json
from pathlib import Path

from jasusi_cli.agent.agent_runtime import AutoApprovePrompter
from jasusi_cli.agent.context_builder import (
    WorkspaceContext,
    _build_file_tree,
    find_project_root,
)
from jasusi_cli.agent.model_selector import (
    ModelSelection,
    get_agent_fallback_chain,
    select_agent_model,
)
from jasusi_cli.agent.output import AgentOutput, AgentStats
from jasusi_cli.agent.prompt import build_agent_system_prompt
from jasusi_cli.cli.entry import build_parser


class TestWorkspaceContext:
    def test_find_project_root_locates_repo_root(self, tmp_path: Path) -> None:
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
        assert find_project_root(sub) == tmp_path

    def test_file_tree_ignores_git_and_target_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("print(1)", encoding="utf-8")
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
        (tmp_path / "target").mkdir()
        (tmp_path / "target" / "bin").write_text("data", encoding="utf-8")

        tree, count = _build_file_tree(tmp_path)
        assert "src/main.py" in tree
        assert ".git" not in tree
        assert "target" not in tree
        assert count == 1

    def test_context_renders_xml_block(self, tmp_path: Path) -> None:
        ctx = WorkspaceContext(
            project_root=tmp_path,
            file_tree="src/main.py",
            git_status="M src/main.py",
            agents_md="# Guidelines",
            instruction_files=["Rule 1"],
            total_files=1,
        )
        block = ctx.to_context_block()
        assert "<workspace_context>" in block
        assert "<file_tree>\nsrc/main.py\n</file_tree>" in block
        assert "<git_status>\nM src/main.py\n</git_status>" in block
        assert "<agents_guidance>\n# Guidelines\n</agents_guidance>" in block
        assert "<project_instructions>\nRule 1\n</project_instructions>" in block
        assert "</workspace_context>" in block


class TestModelSelector:
    def test_select_agent_model_defaults_to_developer_role(self) -> None:
        sel = select_agent_model("developer")
        assert isinstance(sel, ModelSelection)
        assert sel.model_id == "gemini-2.5-flash"
        assert sel.provider == "googleai"

    def test_select_agent_model_with_executor_role(self) -> None:
        sel = select_agent_model("executor")
        assert sel.model_id == "nvidia/nemotron-3-ultra-550b-a35b:free"
        assert sel.provider == "openrouter"

    def test_select_agent_model_with_force_override(self) -> None:
        sel = select_agent_model(force_model="qwen/qwen3-coder-480b-a35b:free")
        assert sel.model_id == "qwen/qwen3-coder-480b-a35b:free"
        assert sel.provider == "openrouter"

    def test_get_agent_fallback_chain_returns_non_empty(self) -> None:
        chain = get_agent_fallback_chain()
        assert len(chain) >= 4
        assert any("qwen" in entry["model"] for entry in chain)


class TestAgentPrompt:
    def test_prompt_contains_operating_principles_and_context(self, tmp_path: Path) -> None:
        ctx = WorkspaceContext(project_root=tmp_path, file_tree="app.py")
        prompt = build_agent_system_prompt(ctx)
        assert "JasusiCLI Agent" in prompt
        assert "file_edit" in prompt
        assert "file_read" in prompt
        assert "<workspace_context>" in prompt
        assert "app.py" in prompt


class TestAgentOutput:
    def test_text_output_header_and_summary(self) -> None:
        buf = io.StringIO()
        out = AgentOutput(fmt="text", stream=buf)
        out.print_header("sess-1", "qwen-coder", "openrouter", Path("/tmp"), fallback_count=8)
        out.print_tool_call("file_read", "test.py", success=True)
        out.print_summary(AgentStats(session_id="sess-1", tool_calls_count=1, input_tokens=100, output_tokens=50))
        rendered = buf.getvalue()
        assert "sess-1" in rendered
        assert "qwen-coder" in rendered
        assert "file_read" in rendered
        assert "✓ Complete" in rendered

    def test_json_output_emits_valid_json_lines(self) -> None:
        buf = io.StringIO()
        out = AgentOutput(fmt="json", stream=buf)
        out.print_header("sess-2", "qwen-coder", "openrouter", Path("/tmp"), fallback_count=4)
        out.print_tool_call("file_write", "test.py", success=True)
        out.print_summary(AgentStats(session_id="sess-2", tool_calls_count=1))
        lines = [json.loads(line) for line in buf.getvalue().strip().split("\n") if line]
        assert lines[0]["type"] == "agent_start"
        assert lines[1]["type"] == "tool_call"
        assert lines[2]["type"] == "agent_complete"


class TestAutoApprovePrompter:
    def test_allows_safe_tools_unconditionally(self) -> None:
        prompter = AutoApprovePrompter(allow_bash=True)
        assert prompter.ask("file_read", "path='app.py'") is True
        assert prompter.ask("file_write", "path='app.py'") is True
        assert prompter.ask("file_edit", "path='app.py'") is True
        assert prompter.ask("glob_search", "pattern='*.py'") is True
        assert prompter.ask("grep_search", "query='def'") is True
        assert prompter.ask("todo_write", "tasks=[]") is True

    def test_allows_bash_when_configured(self) -> None:
        prompter = AutoApprovePrompter(allow_bash=True)
        assert prompter.ask("bash", "pytest") is True


class TestCliAgentParser:
    def test_agent_subcommand_parsed_correctly(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["agent", "Fix", "the", "bug", "--model", "qwen/qwen3-coder-480b-a35b:free", "--auto-approve"])
        assert args.command == "agent"
        assert args.input == ["Fix", "the", "bug"]
        assert args.model == "qwen/qwen3-coder-480b-a35b:free"
        assert args.auto_approve is True
        assert args.max_iterations == 16
