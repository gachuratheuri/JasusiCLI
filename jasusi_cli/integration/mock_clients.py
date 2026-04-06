from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from jasusi_cli.api.client import StreamChunk


@dataclass
class MockTurn:
    text: str = ""
    tool_name: str = ""
    tool_input: dict[str, Any] = field(default_factory=dict)
    tool_use_id: str = "mock-tool-use-1"
    input_tokens: int = 10
    output_tokens: int = 20


class MockApiClient:
    """
    Scriptable mock that replays a queue of MockTurn responses.
    When queue is exhausted, returns a default "Task complete." text turn.
    Records all calls for test assertions.
    """

    DEFAULT_RESPONSE: MockTurn = MockTurn(
        text="Task complete.", input_tokens=5, output_tokens=5,
    )

    def __init__(self, turns: list[MockTurn] | None = None) -> None:
        self._queue: list[MockTurn] = list(turns or [])
        self.calls: list[dict[str, Any]] = []

    def push(self, turn: MockTurn) -> None:
        self._queue.append(turn)

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append({
            "message_count": len(messages),
            "tool_count": len(tools),
            "system_preview": system[:40],
        })
        turn = self._queue.pop(0) if self._queue else self.DEFAULT_RESPONSE
        return self._stream_turn(turn)

    async def _stream_turn(self, turn: MockTurn) -> AsyncIterator[StreamChunk]:
        if turn.tool_name:
            yield StreamChunk(
                delta="",
                tool_name=turn.tool_name,
                tool_input_json=json.dumps(turn.tool_input).encode(),
                tool_use_id=turn.tool_use_id,
                is_tool_call=True,
                input_tokens=turn.input_tokens,
                output_tokens=turn.output_tokens,
                stop_reason="tool_use",
            )
        else:
            half = max(1, len(turn.text) // 2)
            yield StreamChunk(
                delta=turn.text[:half],
                input_tokens=turn.input_tokens,
                output_tokens=0,
                stop_reason=None,
            )
            yield StreamChunk(
                delta=turn.text[half:],
                input_tokens=0,
                output_tokens=turn.output_tokens,
                stop_reason="end_turn",
            )

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def reset(self) -> None:
        self._queue.clear()
        self.calls.clear()


@dataclass
class MockToolCall:
    tool_name: str
    input_json: bytes
    session_id: str
    response: str


class MockToolExecutor:
    """
    Records every execute() call.
    Returns configurable per-tool responses, defaulting to "[mock result: <name>]".
    """

    def __init__(self, responses: dict[str, str] | None = None) -> None:
        self._responses: dict[str, str] = responses or {}
        self.calls: list[MockToolCall] = []

    async def execute(
        self,
        tool_name: str,
        input_json: bytes,
        session_id: str,
    ) -> str:
        response = self._responses.get(tool_name, f"[mock result: {tool_name}]")
        self.calls.append(MockToolCall(
            tool_name=tool_name,
            input_json=input_json,
            session_id=session_id,
            response=response,
        ))
        return response

    def visible_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "bash",
                "description": "Run a shell command",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "cmd"},
                    },
                    "required": ["command"],
                },
            }
        ]

    @property
    def call_count(self) -> int:
        return len(self.calls)

    def was_called(self, tool_name: str) -> bool:
        return any(c.tool_name == tool_name for c in self.calls)

    def reset(self) -> None:
        self.calls.clear()
