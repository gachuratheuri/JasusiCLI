"""Terminal and structured output rendering for JasusiCLI Agent."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from jasusi_cli.config.registry import VERSION


@dataclass
class AgentStats:
    session_id: str
    total_turns: int = 1
    tool_calls_count: int = 0
    files_modified_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class AgentOutput:
    """Renders agent execution events to the terminal or JSON stream."""

    def __init__(self, fmt: str = "text", stream: Any = None) -> None:
        self.fmt = fmt.lower()
        self.stream = stream or sys.stdout
        self._use_color = getattr(self.stream, "isatty", lambda: False)()

    def _c(self, code: str, text: str) -> str:
        if not self._use_color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def cyan(self, text: str) -> str:
        return self._c("36", text)

    def green(self, text: str) -> str:
        return self._c("32", text)

    def yellow(self, text: str) -> str:
        return self._c("33", text)

    def red(self, text: str) -> str:
        return self._c("31", text)

    def bold(self, text: str) -> str:
        return self._c("1", text)

    def dim(self, text: str) -> str:
        return self._c("2", text)

    def print_header(
        self,
        session_id: str,
        model: str,
        provider: str,
        workspace_root: Path,
        fallback_count: int = 0,
    ) -> None:
        if self.fmt == "json":
            evt = {
                "type": "agent_start",
                "session_id": session_id,
                "model": model,
                "provider": provider,
                "workspace": str(workspace_root),
                "fallback_chain_depth": fallback_count,
            }
            print(json.dumps(evt), file=self.stream, flush=True)
            return

        print(f"\n{self.bold('jasusi agent')} {self.dim(f'v{VERSION}')} — {self.cyan('session')} {session_id}", file=self.stream)
        print(f"  {self.dim('workspace:')} {workspace_root}", file=self.stream)
        print(f"  {self.dim('model:')}     {self.bold(model)} ({provider})", file=self.stream)
        if fallback_count > 0:
            print(f"  {self.dim('fallback:')}  {fallback_count} models configured", file=self.stream)
        print(file=self.stream, flush=True)

    def print_delta(self, delta: str) -> None:
        if self.fmt == "json":
            evt = {"type": "text_delta", "delta": delta}
            print(json.dumps(evt), file=self.stream, flush=True)
            return
        self.stream.write(delta)
        self.stream.flush()

    def print_tool_call(self, tool_name: str, preview: str, success: bool = True) -> None:
        if self.fmt == "json":
            evt = {"type": "tool_call", "tool": tool_name, "preview": preview, "success": success}
            print(json.dumps(evt), file=self.stream, flush=True)
            return

        mark = self.green("✓") if success else self.red("✗")
        preview_short = preview.replace("\n", " ")[:80]
        print(f"  {self.cyan(f'[tool: {tool_name}]')} {preview_short} {mark}", file=self.stream, flush=True)

    def print_summary(self, stats: AgentStats) -> None:
        if self.fmt == "json":
            evt = {"type": "agent_complete", "stats": asdict(stats)}
            print(json.dumps(evt), file=self.stream, flush=True)
            return

        cost_str = f"${stats.cost_usd:.4f}"
        print(f"\n{self.green('✓ Complete')} — {stats.tool_calls_count} tool calls executed", file=self.stream)
        print(
            f"  {self.dim('tokens:')} {stats.input_tokens:,} input / {stats.output_tokens:,} output — {cost_str}",
            file=self.stream,
        )
        print(f"  {self.dim('session:')} .jasusi/sessions/{stats.session_id}.jsonl\n", file=self.stream, flush=True)

    def print_error(self, message: str) -> None:
        if self.fmt == "json":
            evt = {"type": "error", "message": message}
            print(json.dumps(evt), file=self.stream, flush=True)
            return
        print(f"\n{self.red('Error:')} {message}\n", file=self.stream, flush=True)
