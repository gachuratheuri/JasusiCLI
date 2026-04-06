"""
Repl — interactive async REPL wired to RuntimeFactory.

Flow per turn:
  1. Read user input (multi-line with \\, submit with Enter)
  2. Check for slash command -> dispatch to CommandHandler
  3. Spin BrailleSpinner while awaiting runtime.submit(user_input)
  4. Stream chunks to OutputFormatter
  5. Record turn in HistoryLog (user + assistant + token delta)
  6. Loop

Ctrl+C during LLM response: cancel stream, print [interrupted], return to prompt.
Ctrl+C at empty prompt: confirm exit.
/compact: calls runtime._compact_history() directly, prints confirmation.
/resume <id>: rebuilds runtime from SessionStore with loaded history.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path
from typing import Any

from jasusi_cli.cli.commands import CommandHandler, CommandResult
from jasusi_cli.cli.history import HistoryLog
from jasusi_cli.cli.output import OutputEvent, OutputFormat, OutputFormatter
from jasusi_cli.cli.spinner import BrailleSpinner
from jasusi_cli.integration.wiring import RuntimeConfig, RuntimeFactory

logger = logging.getLogger(__name__)

_FMT_MAP: dict[str, OutputFormat] = {
    "text": OutputFormat.TEXT,
    "json": OutputFormat.JSON,
    "ndjson": OutputFormat.NDJSON,
}


class Repl:
    """
    Interactive async REPL.
    Builds a ConversationRuntime lazily on first user message.
    """

    def __init__(
        self,
        session_id: str | None = None,
        project: str = "default",
        output_format: OutputFormat | str = OutputFormat.TEXT,
        settings: Any | None = None,
        history_log: HistoryLog | None = None,
        runtime: Any | None = None,
        cwd: Path | None = None,
        simple_mode: bool = False,
    ) -> None:
        self._cwd = cwd or Path.cwd()
        self._simple_mode = simple_mode
        self._session_id = session_id or str(uuid.uuid4())[:12]
        self._project = project

        # Accept both OutputFormat enum and string
        if isinstance(output_format, str):
            self._fmt = _FMT_MAP.get(output_format, OutputFormat.TEXT)
            self._output_format = output_format
        else:
            self._fmt = output_format
            self._output_format = output_format.name.lower()

        self._formatter = OutputFormatter(fmt=self._fmt)
        self._history_log = history_log or HistoryLog(
            self._cwd / ".jasusi" / "history.jsonl",
        )
        self._runtime: Any | None = runtime
        self._worm: Any | None = None
        self._store: Any | None = None
        self._factory = RuntimeFactory(cwd=self._cwd)
        self._commands: CommandHandler | None = CommandHandler(
            session_id=self._session_id,
            project=self._project,
            history_log=self._history_log,
            cwd=self._cwd,
        )
        self._exit_requested = False
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._turn_count: int = 0

    def _ensure_runtime(self) -> None:
        """Build runtime lazily on first user message."""
        if self._runtime is not None:
            return
        cfg = RuntimeConfig(
            session_id=self._session_id,
            project=self._project,
            simple_mode=self._simple_mode,
            cwd=self._cwd,
        )
        self._runtime, self._worm, self._store = self._factory.build(config=cfg)

    async def run(self) -> None:
        """Main REPL loop."""
        self._print_welcome()
        ctrl_c_count = 0

        while not self._exit_requested:
            try:
                user_input = await self._read_input()
            except EOFError:
                break
            except KeyboardInterrupt:
                ctrl_c_count += 1
                if ctrl_c_count >= 2:
                    print("\n[exit]")
                    break
                print("\n(Press Ctrl+C again or /exit to quit)")
                continue

            ctrl_c_count = 0
            user_input = user_input.strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                assert self._commands is not None
                result: CommandResult = self._commands.handle(user_input)
                if result.handled:
                    if result.output:
                        self._formatter.emit(OutputEvent(
                            event_type="status",
                            session_id=self._session_id,
                            content=result.output,
                            metadata={},
                        ))
                    if result.clear_history and self._runtime is not None:
                        self._runtime.clear_history()
                    if result.should_exit:
                        self._exit_requested = True
                    if result.compact_requested and self._runtime is not None:
                        self._runtime._compact_history()
                        count: int = self._runtime.compaction_count
                        print(f"[compacted — count={count}]")
                else:
                    print(result.output or f"[unknown command: {user_input}]")
                continue

            # Regular user message
            await self._process_turn(user_input)

        self._formatter.flush_json()

    async def _process_turn(self, user_input: str) -> None:
        """Submit one user message, stream response, record in history."""
        self._ensure_runtime()
        assert self._runtime is not None

        response_parts: list[str] = []
        interrupted = False

        try:
            async with BrailleSpinner("Thinking", stream=sys.stderr):
                stream = await self._runtime.submit(user_input)

            async for chunk in stream:
                if chunk.delta:
                    self._formatter.emit(OutputEvent(
                        event_type="delta",
                        session_id=self._session_id,
                        content=chunk.delta,
                        metadata={},
                    ))
                    response_parts.append(chunk.delta)
                self._input_tokens += chunk.input_tokens
                self._output_tokens += chunk.output_tokens

        except KeyboardInterrupt:
            interrupted = True
            self._formatter.emit(OutputEvent(
                event_type="status",
                session_id=self._session_id,
                content="\n[interrupted]",
                metadata={},
            ))

        self._formatter.flush_json()
        self._turn_count += 1

        if self._commands is not None:
            self._commands.update_stats(
                self._input_tokens, self._output_tokens, self._turn_count,
            )

        # Record turn in HistoryLog
        assistant_text = "".join(response_parts)
        tokens_used = self._runtime.total_tokens
        self._history_log.append(
            session_id=self._session_id,
            title=f"Turn {self._runtime.turn_count}",
            detail=(
                f"user: {user_input[:60]}\n"
                f"assistant: {assistant_text[:120]}\n"
                f"tokens: {tokens_used}"
                + (" [interrupted]" if interrupted else "")
            ),
            tags=["turn", "interrupted"] if interrupted else ["turn"],
        )

    async def _read_input(self) -> str:
        """
        Read a line of input asynchronously via run_in_executor.
        Supports multi-line input: lines ending with \\ are continued.
        RULE 2: no blocking readline on the event loop thread.
        """
        loop = asyncio.get_event_loop()

        lines: list[str] = []
        while True:
            prompt = "jasusi> " if not lines else "      > "
            line: str = await loop.run_in_executor(None, input, prompt)
            if line.endswith("\\"):
                lines.append(line[:-1])
            else:
                lines.append(line)
                break

        return "\n".join(lines)

    def _print_welcome(self) -> None:
        from jasusi_cli.bootstrap.graph import BootstrapGraph

        print(
            f"jasusi {BootstrapGraph.VERSION}"
            f" — type /help for commands, /exit to quit",
        )
        if self._session_id:
            print(f"Resuming session: {self._session_id}")
