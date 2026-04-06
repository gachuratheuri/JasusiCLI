"""
ConversationRuntime[C, T] — the central turn loop.
Generic over ApiClientProtocol[C] and ToolExecutorProtocol[T].

Turn budget:
  max_turns           = 8   (Python query-engine level — per user message)
  MAX_ITERATIONS      = 16  (Rust runtime level — inner tool-call loop per turn)
  compact_after_turns = 12  (accumulated turns triggering compaction)

Three-stage compaction:
  Stage 1 (soft, 4000 tokens):  silent NO_REPLY memory flush to WormLedger
  Stage 2 (main, 10000 tokens): strip analysis tags, preserve 4 recent, 160-char summary
  Stage 3 (deep, 50000 tokens): structured 2000-token summary
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Generic, Protocol, TypeVar

from jasusi_cli.api.client import StreamChunk

logger = logging.getLogger(__name__)


class ApiClientProtocol(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[StreamChunk]: ...


class ToolExecutorProtocol(Protocol):
    async def execute(
        self,
        tool_name: str,
        input_json: bytes,
        session_id: str,
    ) -> str: ...

    def visible_schemas(self) -> list[dict[str, Any]]: ...


C = TypeVar("C")
T = TypeVar("T")


@dataclass
class TextBlock:
    text: str


@dataclass
class ToolUseBlock:
    tool_use_id: str
    tool_name: str
    input_json: bytes


@dataclass
class ToolResultBlock:
    tool_use_id: str
    content: str
    is_error: bool = False


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock


@dataclass
class Message:
    role: str   # "user" | "assistant"
    content: list[ContentBlock]

    def to_api_dict(self) -> dict[str, Any]:
        import json as _json

        parts: list[dict[str, Any]] = []
        for block in self.content:
            if isinstance(block, TextBlock):
                parts.append({"type": "text", "text": block.text})
            elif isinstance(block, ToolUseBlock):
                parts.append({
                    "type": "tool_use",
                    "id": block.tool_use_id,
                    "name": block.tool_name,
                    "input": _json.loads(block.input_json),
                })
            elif isinstance(block, ToolResultBlock):
                parts.append({
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                })
        return {"role": self.role, "content": parts}


@dataclass
class QueryEngineConfig:
    max_turns: int = 8
    max_budget_tokens: int = 2_000
    compact_after_turns: int = 12


class ConversationRuntime(Generic[C, T]):
    """
    The central turn loop. Injectable api_client and tool_executor.
    submit(user_input) -> AsyncIterator[StreamChunk]
    """

    MAX_ITERATIONS: int = 16

    def __init__(
        self,
        api_client: Any,
        tool_executor: Any,
        session_id: str,
        system_prompt: str = "",
        max_turns: int = 8,
        max_budget_tokens: int = 2_000,
        compact_after_turns: int = 12,
    ) -> None:
        self._client = api_client
        self._executor = tool_executor
        self._session_id = session_id
        self._system = system_prompt
        self._config = QueryEngineConfig(
            max_turns=max_turns,
            max_budget_tokens=max_budget_tokens,
            compact_after_turns=compact_after_turns,
        )
        self._history: list[Message] = []
        self._turn_count: int = 0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._compaction_count: int = 0

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def total_tokens(self) -> int:
        return self._total_input_tokens + self._total_output_tokens

    @property
    def compaction_count(self) -> int:
        return self._compaction_count

    def clear_history(self) -> None:
        self._history.clear()
        self._turn_count = 0

    async def submit(self, user_input: str) -> AsyncIterator[StreamChunk]:
        """
        Process one user message. Returns an async iterator of StreamChunks.
        submit() is async so callers can await it to get the iterator,
        then async-for over the iterator for streaming output.
        """
        return self._run_turn(user_input)

    async def _run_turn(
        self, user_input: str,
    ) -> AsyncIterator[StreamChunk]:
        if self._turn_count >= self._config.max_turns:
            yield StreamChunk(
                delta="[turn limit reached — start a new session]",
                stop_reason="max_turns",
            )
            return

        self._history.append(Message(
            role="user",
            content=[TextBlock(text=user_input)],
        ))

        tools: list[dict[str, Any]] = self._executor.visible_schemas()
        iterations = 0

        while iterations < self.MAX_ITERATIONS:
            iterations += 1
            messages_dicts = [m.to_api_dict() for m in self._history]

            stream: AsyncIterator[StreamChunk] = await self._client.complete(
                messages=messages_dicts,
                tools=tools,
                system=self._system,
            )

            iter_text = ""
            iter_tool_calls: list[tuple[str, bytes, str]] = []
            stop_reason = "end_turn"

            async for chunk in stream:
                self._total_input_tokens += chunk.input_tokens
                self._total_output_tokens += chunk.output_tokens
                if chunk.stop_reason:
                    stop_reason = chunk.stop_reason
                if chunk.is_tool_call and chunk.tool_name:
                    iter_tool_calls.append((
                        chunk.tool_name,
                        chunk.tool_input_json or b"{}",
                        chunk.tool_use_id or f"use-{iterations}",
                    ))
                else:
                    iter_text += chunk.delta
                    if chunk.delta:
                        yield chunk

            assistant_blocks: list[ContentBlock] = []
            if iter_text:
                assistant_blocks.append(TextBlock(text=iter_text))
            for tool_name, input_json, use_id in iter_tool_calls:
                assistant_blocks.append(ToolUseBlock(
                    tool_use_id=use_id,
                    tool_name=tool_name,
                    input_json=input_json,
                ))
            if assistant_blocks:
                self._history.append(Message(
                    role="assistant", content=assistant_blocks,
                ))

            if not iter_tool_calls:
                break

            tool_result_blocks: list[ContentBlock] = []
            for tool_name, input_json, use_id in iter_tool_calls:
                result = await self._executor.execute(
                    tool_name, input_json, self._session_id,
                )
                tool_result_blocks.append(ToolResultBlock(
                    tool_use_id=use_id,
                    content=result,
                    is_error=(
                        result.startswith("[error]")
                        or result.startswith("[permission")
                    ),
                ))
            self._history.append(Message(
                role="user", content=tool_result_blocks,
            ))

        self._turn_count += 1
        logger.debug(
            "ConversationRuntime: session=%s turn=%d tokens=%d/%d iterations=%d",
            self._session_id,
            self._turn_count,
            self._total_input_tokens,
            self._total_output_tokens,
            iterations,
        )
        await self._maybe_compact()

    async def _maybe_compact(self) -> None:
        if (
            self._turn_count > 0
            and self._turn_count % self._config.compact_after_turns == 0
        ):
            self._compact_history()

    def _compact_history(self) -> None:
        preserve_recent = 4
        if len(self._history) <= preserve_recent:
            return
        old_messages = self._history[:-preserve_recent]
        recent_messages = self._history[-preserve_recent:]
        all_text = " ".join(
            block.text
            for msg in old_messages
            for block in msg.content
            if isinstance(block, TextBlock)
        )
        summary = all_text[:157] + "..." if len(all_text) > 160 else all_text
        summary_message = Message(
            role="user",
            content=[TextBlock(text=f"[compacted context: {summary}]")],
        )
        self._history = [summary_message] + recent_messages
        self._compaction_count += 1
        logger.info(
            "ConversationRuntime: compacted session=%s count=%d history_len=%d",
            self._session_id,
            self._compaction_count,
            len(self._history),
        )
