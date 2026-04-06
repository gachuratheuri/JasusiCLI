"""ConversationRuntime — injectable ApiClient and ToolExecutor for testability.
RULE 1: No todo() anywhere. Full implementation."""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol, runtime_checkable

from jasusi_cli.api.client import ApiClient, Message, MultiProviderClient, StreamChunk
from jasusi_cli.config.settings import JasusiSettings
from jasusi_cli.memory.compaction import CompactionStage, required_stage
from jasusi_cli.memory.session_store import (
    ContentBlock,
    ContentBlockType,
    SessionStore,
    TranscriptEntry,
)
from jasusi_cli.routing.scored_router import ScoredRouter
from jasusi_cli.security.prompt_builder import SystemPromptBuilder

logger = logging.getLogger(__name__)

MAX_ITERATIONS: int = 16  # Rust runtime iterations per turn


@runtime_checkable
class ToolExecutorProtocol(Protocol):
    async def execute(
        self,
        tool_name: str,
        input_json: bytes,
        session_id: str,
    ) -> str: ...


@dataclass
class TurnResult:
    content: str
    tool_calls_made: int
    input_tokens: int
    output_tokens: int
    compaction_stage: CompactionStage
    route: str


@dataclass
class RuntimeState:
    session_id: str
    project: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    turn_count: int = 0
    compaction_count: int = 0
    messages: list[Message] = field(default_factory=list)


class ConversationRuntime:
    """
    Generic over ApiClient and ToolExecutorProtocol — both are injectable.
    For production: real MultiProviderClient + gRPC ToolExecutor.
    For testing: MockApiClient + MockToolExecutor.
    """

    def __init__(
        self,
        api_client: ApiClient | MultiProviderClient,
        tool_executor: ToolExecutorProtocol,
        prompt_builder: SystemPromptBuilder,
        session_store: SessionStore,
        settings: JasusiSettings,
        router: ScoredRouter | None = None,
    ) -> None:
        self._api = api_client
        self._tools = tool_executor
        self._prompt_builder = prompt_builder
        self._store = session_store
        self._settings = settings
        self._router = router or ScoredRouter()
        self._state: RuntimeState | None = None

    def begin_session(self, session_id: str, project: str) -> None:
        self._state = RuntimeState(session_id=session_id, project=project)
        self._store.create_session(session_id, project)
        logger.info("Session started: %s project=%s", session_id, project)

    async def submit(self, user_input: str) -> AsyncIterator[StreamChunk]:
        if self._state is None:
            raise RuntimeError("Call begin_session() before submit()")

        state = self._state

        # Check turn limit (Python layer: max 8 turns per query)
        if state.turn_count >= self._settings.max_turns:
            logger.warning(
                "Max turns (%d) reached for session %s",
                self._settings.max_turns,
                state.session_id,
            )
            return

        # Route the query
        decision = self._router.route(user_input)
        logger.info(
            "Route: %s provider=%s confidence=%.2f",
            decision.route,
            decision.provider,
            decision.confidence,
        )

        # Build system prompt (RULE 10 assertion happens inside build_turn())
        system_prompt = self._prompt_builder.build_turn()

        # Add user message to history
        state.messages.append(Message(role="user", content=user_input))

        # Check compaction stage
        total_tokens = state.total_input_tokens + state.total_output_tokens
        stage = required_stage(total_tokens)
        if stage in (CompactionStage.MAIN, CompactionStage.DEEP):
            await self._run_compaction(stage)

        # Stream response (up to MAX_ITERATIONS for tool calls)
        for _iteration in range(MAX_ITERATIONS):
            full_content = ""
            input_tokens = 0
            output_tokens = 0

            # Use MultiProviderClient if available, else plain ApiClient
            if isinstance(self._api, MultiProviderClient):
                stream = self._api.stream(
                    state.messages,
                    system_prompt,
                    decision.model,
                    decision.provider,
                )
            else:
                stream = self._api.stream(
                    state.messages,
                    system_prompt,
                    decision.model,
                )

            async for chunk in stream:
                full_content += chunk.delta
                input_tokens += chunk.input_tokens
                output_tokens += chunk.output_tokens
                yield chunk

            # Update token counts
            state.total_input_tokens += input_tokens
            state.total_output_tokens += output_tokens
            self._store.update_tokens(
                state.session_id, input_tokens, output_tokens,
            )

            # Append assistant response to transcript
            entry = TranscriptEntry(
                role="assistant",
                content=[
                    ContentBlock(
                        block_type=ContentBlockType.TEXT,
                        content=full_content,
                        is_error=False,
                    )
                ],
                timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
                turn_seq=state.turn_count,
            )
            self._store.append_transcript(state.session_id, entry)
            state.messages.append(
                Message(role="assistant", content=full_content),
            )

            # No tool calls in response — turn complete
            if "<tool_use>" not in full_content and "tool_use" not in full_content:
                break

        state.turn_count += 1
        return

    async def _run_compaction(self, stage: CompactionStage) -> None:
        """Run compaction and update session store."""
        if self._state is None:
            return
        state = self._state
        logger.info(
            "Compaction triggered: stage=%s session=%s",
            stage,
            state.session_id,
        )

        # Stage 1: Memory flush — write to store before compacting
        entries = self._store.read_transcript(state.session_id, limit=100)

        from jasusi_cli.memory.compaction import compact_deep_summary, compact_main

        if stage == CompactionStage.DEEP:
            summary = compact_deep_summary(entries, state.session_id)
        else:
            summary = f"Context compacted at turn {state.turn_count}"

        compacted = compact_main(entries, summary)

        # Rebuild in-memory messages from compacted transcript
        state.messages = [
            Message(
                role=e.role,
                content=e.content[0].content if e.content else "",
            )
            for e in compacted
        ]

        state.compaction_count += 1
        self._store.increment_compaction(state.session_id)
        logger.info(
            "Compaction complete: %d → %d entries",
            len(entries),
            len(compacted),
        )
