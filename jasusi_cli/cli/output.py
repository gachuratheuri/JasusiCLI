from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from enum import Enum, auto
from typing import Any, TextIO


class OutputFormat(Enum):
    TEXT = auto()
    JSON = auto()
    NDJSON = auto()


@dataclass
class OutputEvent:
    event_type: str          # "delta" | "tool_call" | "tool_result" | "error" | "status"
    session_id: str
    content: str
    metadata: dict[str, Any]


class OutputFormatter:
    """
    Writes OutputEvents to a stream in the configured format.
    NDJSON: one JSON object per line — composable in shell pipelines.
    """

    def __init__(
        self,
        fmt: OutputFormat = OutputFormat.TEXT,
        stream: TextIO | None = None,
    ) -> None:
        self._fmt = fmt
        self._stream: TextIO = stream or sys.stdout
        self._json_buffer: list[dict[str, Any]] = []

    def emit(self, event: OutputEvent) -> None:
        if self._fmt == OutputFormat.TEXT:
            self._emit_text(event)
        elif self._fmt == OutputFormat.NDJSON:
            self._emit_ndjson(event)
        elif self._fmt == OutputFormat.JSON:
            self._json_buffer.append(self._event_to_dict(event))

    def flush_json(self) -> None:
        if self._fmt == OutputFormat.JSON:
            json.dump(self._json_buffer, self._stream, indent=2)
            self._stream.write("\n")
            self._stream.flush()
            self._json_buffer.clear()

    def _emit_text(self, event: OutputEvent) -> None:
        if event.event_type == "delta":
            self._stream.write(event.content)
            self._stream.flush()
        elif event.event_type == "tool_call":
            self._stream.write(f"\n[tool: {event.metadata.get('tool', '?')}]\n")
            self._stream.flush()
        elif event.event_type == "tool_result":
            preview = event.content[:120]
            self._stream.write(f"[result: {preview}]\n")
            self._stream.flush()
        elif event.event_type == "error":
            self._stream.write(f"\n[error] {event.content}\n")
            self._stream.flush()
        elif event.event_type == "status":
            self._stream.write(f"\n{event.content}\n")
            self._stream.flush()

    def _emit_ndjson(self, event: OutputEvent) -> None:
        self._stream.write(json.dumps(self._event_to_dict(event)))
        self._stream.write("\n")
        self._stream.flush()

    @staticmethod
    def _event_to_dict(event: OutputEvent) -> dict[str, Any]:
        return asdict(event)
