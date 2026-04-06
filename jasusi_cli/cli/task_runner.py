"""TaskRunner — one-shot task execution without interactive REPL."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from jasusi_cli.cli.output import OutputEvent, OutputFormat, OutputFormatter
from jasusi_cli.integration.wiring import RuntimeConfig, RuntimeFactory

logger = logging.getLogger(__name__)


class TaskRunner:
    """
    Runs a single task turn against the runtime and returns an exit code.
    Uses RuntimeFactory to build the runtime with default or injected clients.
    """

    def __init__(self, cwd: Path | None = None) -> None:
        self._cwd = cwd or Path.cwd()

    def run(
        self,
        task_input: str = "",
        output_format: str = "text",
        simple_mode: bool = False,
    ) -> int:
        """Execute a single task turn. Returns 0 on success, 1 on error."""
        try:
            return asyncio.run(
                self._run_async(task_input, output_format, simple_mode),
            )
        except Exception:
            logger.exception("TaskRunner failed")
            return 1

    async def _run_async(
        self,
        task_input: str,
        output_format: str,
        simple_mode: bool,
    ) -> int:
        fmt_map: dict[str, OutputFormat] = {
            "text": OutputFormat.TEXT,
            "json": OutputFormat.JSON,
            "ndjson": OutputFormat.NDJSON,
        }
        fmt = fmt_map.get(output_format, OutputFormat.TEXT)
        formatter = OutputFormatter(fmt=fmt)

        factory = RuntimeFactory(cwd=self._cwd)
        cfg = RuntimeConfig(
            cwd=self._cwd,
            simple_mode=simple_mode,
            task_input=task_input,
        )
        runtime, _worm, _store = factory.build(config=cfg)

        stream = await runtime.submit(task_input)
        async for chunk in stream:
            formatter.emit(OutputEvent(
                event_type="delta",
                session_id=cfg.session_id,
                content=chunk.delta,
                metadata={},
            ))
        formatter.flush_json()
        return 0
