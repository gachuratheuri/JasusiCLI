from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class CommandSource(Enum):
    BUILTIN = auto()
    INTERNAL_ONLY = auto()
    FEATURE_GATED = auto()


@dataclass
class CommandDef:
    name: str
    description: str
    source: CommandSource
    aliases: list[str]


COMMAND_REGISTRY: list[CommandDef] = [
    CommandDef("help",        "Show available commands",              CommandSource.BUILTIN, ["?"]),
    CommandDef("status",      "Show session status and token usage",  CommandSource.BUILTIN, []),
    CommandDef("compact",     "Trigger manual compaction",            CommandSource.BUILTIN, []),
    CommandDef("model",       "Show or switch current model",         CommandSource.BUILTIN, []),
    CommandDef("permissions", "Show current permission policy",       CommandSource.BUILTIN, []),
    CommandDef("clear",       "Clear conversation history",           CommandSource.BUILTIN, ["cls"]),
    CommandDef("cost",        "Show accumulated token cost",          CommandSource.BUILTIN, []),
    CommandDef("resume",      "Resume a previous session by ID",      CommandSource.BUILTIN, []),
    CommandDef("config",      "Show loaded configuration",            CommandSource.BUILTIN, []),
    CommandDef("memory",      "Show or edit JASUSI.md files",         CommandSource.BUILTIN, []),
    CommandDef("init",        "Initialise JASUSI.md in current dir",  CommandSource.BUILTIN, []),
    CommandDef("exit",        "Exit jasusi",                          CommandSource.BUILTIN, ["quit", "q"]),
    CommandDef("diff",        "Show git diff of session changes",     CommandSource.BUILTIN, []),
    CommandDef("version",     "Show jasusi version",                  CommandSource.BUILTIN, ["ver"]),
    CommandDef("history",     "Show session history as Markdown",     CommandSource.BUILTIN, ["log"]),
]

_CMD_INDEX: dict[str, CommandDef] = {}
for _cmd in COMMAND_REGISTRY:
    _CMD_INDEX[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        _CMD_INDEX[_alias] = _cmd


@dataclass
class CommandResult:
    handled: bool
    output: str
    should_exit: bool = False
    clear_history: bool = False


class CommandHandler:
    VERSION: str = "0.1.0"

    def __init__(
        self,
        session_id: str,
        project: str,
        settings_repr: str = "",
        history_log: Any | None = None,
        session_store: Any | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._session_id = session_id
        self._project = project
        self._settings_repr = settings_repr
        self._history_log = history_log
        self._session_store = session_store
        self._cwd = cwd or Path.cwd()
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._turn_count: int = 0
        self._current_model: str = "nvidia/llama-3.3-nemotron-super-49b-v1"
        self._current_provider: str = "nemotron"

    def update_stats(
        self, input_tokens: int, output_tokens: int, turn_count: int,
    ) -> None:
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._turn_count = turn_count

    def update_model(self, provider: str, model: str) -> None:
        self._current_provider = provider
        self._current_model = model

    def handle(self, line: str) -> CommandResult:
        stripped = line.strip()
        if not stripped.startswith("/"):
            return CommandResult(handled=False, output="")
        parts = stripped[1:].split(maxsplit=1)
        cmd_name = parts[0].lower() if parts else ""
        args = parts[1] if len(parts) > 1 else ""
        cmd_def = _CMD_INDEX.get(cmd_name)
        if cmd_def is None:
            return CommandResult(
                handled=True,
                output=f"Unknown command: /{cmd_name}. Type /help for commands.",
            )
        return self._dispatch(cmd_def.name, args)

    def _dispatch(self, name: str, args: str) -> CommandResult:
        dispatch: dict[str, Any] = {
            "help":        self._cmd_help,
            "status":      self._cmd_status,
            "compact":     self._cmd_compact,
            "model":       self._cmd_model,
            "permissions": self._cmd_permissions,
            "clear":       self._cmd_clear,
            "cost":        self._cmd_cost,
            "resume":      self._cmd_resume,
            "config":      self._cmd_config,
            "memory":      self._cmd_memory,
            "init":        self._cmd_init,
            "exit":        self._cmd_exit,
            "diff":        self._cmd_diff,
            "version":     self._cmd_version,
            "history":     self._cmd_history,
        }
        fn = dispatch.get(name)
        if fn is None:
            return CommandResult(handled=True, output=f"[not implemented: /{name}]")
        result: CommandResult = fn(args)
        return result

    def _cmd_help(self, _args: str) -> CommandResult:
        lines = ["Available commands:\n"]
        for cmd in COMMAND_REGISTRY:
            aliases = (
                f" ({', '.join('/' + a for a in cmd.aliases)})" if cmd.aliases else ""
            )
            lines.append(f"  /{cmd.name:<14} {cmd.description}{aliases}")
        return CommandResult(handled=True, output="\n".join(lines))

    def _cmd_status(self, _args: str) -> CommandResult:
        total = self._input_tokens + self._output_tokens
        output = (
            f"Session:  {self._session_id}\n"
            f"Project:  {self._project}\n"
            f"Model:    {self._current_model} ({self._current_provider})\n"
            f"Turns:    {self._turn_count}\n"
            f"Tokens:   {self._input_tokens:,} in / "
            f"{self._output_tokens:,} out / {total:,} total\n"
        )
        return CommandResult(handled=True, output=output)

    def _cmd_cost(self, _args: str) -> CommandResult:
        input_rate = 15.0 / 1_000_000
        output_rate = 75.0 / 1_000_000
        cost = (self._input_tokens * input_rate) + (self._output_tokens * output_rate)
        output = (
            f"Input:    {self._input_tokens:,} tokens  "
            f"(${self._input_tokens * input_rate:.4f})\n"
            f"Output:   {self._output_tokens:,} tokens  "
            f"(${self._output_tokens * output_rate:.4f})\n"
            f"Total:    ${cost:.4f}\n"
        )
        return CommandResult(handled=True, output=output)

    def _cmd_version(self, _args: str) -> CommandResult:
        return CommandResult(handled=True, output=f"jasusi v{self.VERSION}")

    def _cmd_clear(self, _args: str) -> CommandResult:
        return CommandResult(
            handled=True, output="[history cleared]", clear_history=True,
        )

    def _cmd_exit(self, _args: str) -> CommandResult:
        return CommandResult(handled=True, output="Goodbye.", should_exit=True)

    def _cmd_model(self, args: str) -> CommandResult:
        if args.strip():
            self._current_model = args.strip()
            return CommandResult(
                handled=True,
                output=f"Model switched to: {self._current_model}",
            )
        return CommandResult(
            handled=True,
            output=(
                f"Current model: {self._current_model} "
                f"(provider: {self._current_provider})"
            ),
        )

    def _cmd_permissions(self, _args: str) -> CommandResult:
        output = (
            "Permission defaults:\n"
            "  ALLOW:  file_read, glob_search, grep_search, "
            "web_fetch, web_search, todo_write\n"
            "  PROMPT: bash, file_write, file_edit\n"
        )
        return CommandResult(handled=True, output=output)

    def _cmd_compact(self, _args: str) -> CommandResult:
        return CommandResult(
            handled=True,
            output=f"[compaction requested at turn {self._turn_count}]",
        )

    def _cmd_resume(self, args: str) -> CommandResult:
        session_id = args.strip()
        if not session_id and self._session_store is not None:
            sessions: list[Any] = self._session_store.list_sessions()
            if not sessions:
                return CommandResult(handled=True, output="No sessions to resume.")
            lines = ["Recent sessions:"]
            for s in sorted(
                sessions, key=lambda x: x.updated_at, reverse=True,
            )[:10]:
                lines.append(
                    f"  {s.session_id[:12]}  {s.project:<20}  turns={s.turn_count}"
                )
            return CommandResult(handled=True, output="\n".join(lines))
        return CommandResult(
            handled=True,
            output=f"[resume] Use --resume {session_id} when starting jasusi",
        )

    def _cmd_config(self, _args: str) -> CommandResult:
        if self._settings_repr:
            return CommandResult(
                handled=True,
                output=f"Configuration:\n{self._settings_repr}",
            )
        return CommandResult(handled=True, output="[config] No settings loaded")

    def _cmd_memory(self, _args: str) -> CommandResult:
        jasusi_md = self._cwd / "JASUSI.md"
        if jasusi_md.exists():
            content = jasusi_md.read_text(encoding="utf-8")
            return CommandResult(handled=True, output=f"# JASUSI.md\n\n{content}")
        return CommandResult(
            handled=True,
            output="No JASUSI.md in current directory. Use /init to create one.",
        )

    def _cmd_init(self, _args: str) -> CommandResult:
        jasusi_md = self._cwd / "JASUSI.md"
        if jasusi_md.exists():
            return CommandResult(
                handled=True, output=f"JASUSI.md already exists at {jasusi_md}",
            )
        template = (
            "# Project Instructions\n\n"
            "## Rules\n"
            "- Always write tests after implementing code\n"
            "- Never use todo!() or unimplemented!()\n"
            "- Use atomic file writes (tempfile + rename)\n\n"
            "## Project Context\n"
            "_Add your project-specific context here._\n"
        )
        jasusi_md.write_text(template, encoding="utf-8")
        return CommandResult(handled=True, output=f"Created JASUSI.md at {jasusi_md}")

    def _cmd_diff(self, _args: str) -> CommandResult:
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                capture_output=True,
                text=True,
                timeout=10,
                shell=False,
                cwd=str(self._cwd),
            )
            diff = result.stdout.strip()
            if not diff:
                return CommandResult(
                    handled=True, output="[no changes since session start]",
                )
            if len(diff) > 4096:
                diff = diff[:4096] + "\n[diff truncated]"
            return CommandResult(handled=True, output=diff)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return CommandResult(
                handled=True,
                output="[diff unavailable — git not found or timed out]",
            )
        except OSError as e:
            return CommandResult(handled=True, output=f"[diff error] {e}")

    def _cmd_history(self, args: str) -> CommandResult:
        if self._history_log is None:
            return CommandResult(
                handled=True, output="[history log not available]",
            )
        limit_str = args.strip()
        limit = int(limit_str) if limit_str.isdigit() else 20
        md: str = self._history_log.to_markdown(limit=limit)
        return CommandResult(handled=True, output=md)
