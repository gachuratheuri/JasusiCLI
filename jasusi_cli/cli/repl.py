from __future__ import annotations

import asyncio
import logging
import sys
import uuid
from pathlib import Path

from jasusi_cli.cli.commands import CommandHandler
from jasusi_cli.cli.history import HistoryLog
from jasusi_cli.cli.output import OutputEvent, OutputFormat, OutputFormatter
from jasusi_cli.config.settings import JasusiSettings

logger = logging.getLogger(__name__)

BRAILLE_SPINNER: list[str] = [
    "\u2819", "\u2839", "\u2838", "\u283c", "\u2834", "\u2826", "\u2827", "\u2807", "\u280f", "\u280b",
]
PROMPT: str = "\n> "


class Repl:
    def __init__(
        self,
        session_id: str | None = None,
        project: str = "default",
        output_format: OutputFormat = OutputFormat.TEXT,
        settings: JasusiSettings | None = None,
        history_log: HistoryLog | None = None,
        runtime: object | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._session_id = session_id or str(uuid.uuid4())[:12]
        self._project = project
        self._formatter = OutputFormatter(fmt=output_format)
        self._settings = settings or JasusiSettings(providers=[])
        self._history = history_log or HistoryLog()
        self._runtime = runtime
        self._cwd = cwd or Path.cwd()
        self._cmd_handler = CommandHandler(
            session_id=self._session_id,
            project=self._project,
            history_log=self._history,
            cwd=self._cwd,
        )
        self._running = False
        self._input_tokens: int = 0
        self._output_tokens: int = 0
        self._turn_count: int = 0

    async def run(self) -> None:
        self._running = True
        self._formatter.emit(OutputEvent(
            event_type="status",
            session_id=self._session_id,
            content=f"jasusi v{CommandHandler.VERSION} — session {self._session_id}",
            metadata={},
        ))
        while self._running:
            try:
                user_input = await self._read_input()
            except (EOFError, KeyboardInterrupt):
                self._formatter.emit(OutputEvent(
                    event_type="status",
                    session_id=self._session_id,
                    content="\nInterrupted. Type /exit to quit.",
                    metadata={},
                ))
                continue
            if not user_input.strip():
                continue
            cmd_result = self._cmd_handler.handle(user_input)
            if cmd_result.handled:
                self._formatter.emit(OutputEvent(
                    event_type="status",
                    session_id=self._session_id,
                    content=cmd_result.output,
                    metadata={},
                ))
                if cmd_result.should_exit:
                    self._running = False
                    break
                continue
            await self._run_turn(user_input)
        self._formatter.flush_json()

    async def _read_input(self) -> str:
        loop = asyncio.get_event_loop()

        def _input(prompt: str) -> str:
            sys.stdout.write(prompt)
            sys.stdout.flush()
            return input()

        lines: list[str] = []
        prompt = PROMPT
        while True:
            line = await loop.run_in_executor(None, _input, prompt)
            if line.endswith("\\"):
                lines.append(line[:-1])
                prompt = "... "
            else:
                lines.append(line)
                break
        return "\n".join(lines)

    async def _run_turn(self, user_input: str) -> None:
        if self._runtime is None:
            self._formatter.emit(OutputEvent(
                event_type="error",
                session_id=self._session_id,
                content="No runtime connected. Start with: jasusi task <description>",
                metadata={},
            ))
            return
        self._history.append(
            session_id=self._session_id,
            title=f"Turn {self._turn_count + 1}",
            detail=user_input[:120],
            tags=["turn"],
        )
        spinner_task = asyncio.create_task(self._spin())
        try:
            async for chunk in self._runtime.submit(user_input):  # type: ignore[attr-defined]
                spinner_task.cancel()
                self._formatter.emit(OutputEvent(
                    event_type="delta",
                    session_id=self._session_id,
                    content=chunk.delta,
                    metadata={},
                ))
                self._input_tokens += chunk.input_tokens
                self._output_tokens += chunk.output_tokens
        finally:
            spinner_task.cancel()
            try:
                await spinner_task
            except asyncio.CancelledError:
                pass
        self._turn_count += 1
        self._cmd_handler.update_stats(
            self._input_tokens, self._output_tokens, self._turn_count,
        )

    async def _spin(self) -> None:
        try:
            i = 0
            while True:
                frame = BRAILLE_SPINNER[i % len(BRAILLE_SPINNER)]
                sys.stdout.write(f"\r{frame} thinking...")
                sys.stdout.flush()
                await asyncio.sleep(0.1)
                i += 1
        except asyncio.CancelledError:
            sys.stdout.write("\r" + " " * 16 + "\r")
            sys.stdout.flush()
