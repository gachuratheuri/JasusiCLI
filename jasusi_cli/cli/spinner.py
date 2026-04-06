"""
BrailleSpinner — 10-frame braille animation that runs as an asyncio Task.
Cancels cleanly when the LLM response stream completes.
RULE 2: fully async — uses asyncio.sleep, writes to sys.stderr.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TextIO

SPINNER_FRAMES: tuple[str, ...] = (
    "\u2819", "\u2819", "\u2839", "\u2838", "\u283c",
    "\u2834", "\u2826", "\u2827", "\u2807", "\u280f",
)
SPINNER_INTERVAL: float = 0.08  # seconds per frame


class BrailleSpinner:
    """
    Async braille spinner that runs in a background asyncio Task.

    Usage:
        async with BrailleSpinner("Thinking"):
            result = await long_running_operation()
    """

    def __init__(self, label: str = "Thinking", stream: TextIO | None = None) -> None:
        self._label = label
        self._stream = stream or sys.stderr
        self._task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> BrailleSpinner:
        self._task = asyncio.create_task(self._spin())
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Clear the spinner line
        self._stream.write("\r\033[K")
        self._stream.flush()

    async def _spin(self) -> None:
        frame_idx = 0
        while True:
            frame = SPINNER_FRAMES[frame_idx % len(SPINNER_FRAMES)]
            self._stream.write(f"\r{frame} {self._label}...")
            self._stream.flush()
            await asyncio.sleep(SPINNER_INTERVAL)
            frame_idx += 1
