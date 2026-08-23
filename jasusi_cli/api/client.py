"""Multi-provider async client with 7-status-code exponential backoff retry."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jasusi_cli.api.provider_client import ProviderClient as ProviderClient

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES: frozenset[int] = frozenset(
    {408, 409, 429, 500, 502, 503, 504}
)
INITIAL_BACKOFF_S: float = 0.2
MAX_BACKOFF_S: float = 2.0
MAX_RETRIES: int = 2

# Provider fallback chain — fires on 429 only
PROVIDER_FALLBACK_CHAIN: dict[str, str] = {
    "nemotron": "gemini",
    "gemini": "kimi",
    "kimi": "deepseek",
    "deepseek": "kimi",
}


@dataclass
class Message:
    role: str
    content: str


@dataclass
class StreamChunk:
    delta: str = ""
    is_final: bool = False
    tool_name: str = ""
    tool_input_json: bytes = b""
    tool_use_id: str = ""
    is_tool_call: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None


class ProviderError(Exception):
    def __init__(self, provider: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code

    def is_retryable(self) -> bool:
        return self.status_code in RETRYABLE_STATUS_CODES

    def is_rate_limited(self) -> bool:
        return self.status_code == 429


class ApiClient:
    """Base protocol — injectable for testing."""

    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
        model: str,
        max_tokens: int = 8192,
    ) -> AsyncIterator[StreamChunk]:
        raise NotImplementedError
        # Make mypy happy with the async generator protocol
        if False:  # pragma: no cover
            yield StreamChunk(delta="", is_final=True)


class MultiProviderClient:
    """
    Wraps multiple provider clients. On 429, follows PROVIDER_FALLBACK_CHAIN.
    On other retryable errors, retries with exponential backoff up to MAX_RETRIES.
    """

    def __init__(
        self,
        provider_clients: dict[str, ApiClient],
        default_provider: str | None = None,
    ) -> None:
        if not provider_clients:
            raise ValueError(
                "MultiProviderClient requires at least one configured provider; "
                "check that a provider API key is set",
            )
        self._clients = provider_clients
        self._default_provider = default_provider or next(iter(provider_clients))
        if self._default_provider not in provider_clients:
            raise ValueError(
                f"default provider {self._default_provider!r} has no configured client",
            )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[StreamChunk]:
        """Conform to ``ApiClientProtocol``.

        ``ConversationRuntime`` drives every turn through ``complete``. This
        adapter previously exposed only ``stream`` with a different signature,
        so the default-wired runtime raised ``AttributeError`` on its first
        turn. Provider selection and fallback stay inside this class.
        """
        client = self._clients[self._default_provider]
        completer = getattr(client, "complete", None)
        if completer is not None:
            async for chunk in completer(messages, tools, system):
                yield chunk
            return

        # Legacy ApiClient shape: adapt to the streaming signature.
        async for chunk in self.stream(
            messages=messages,  # type: ignore[arg-type]
            system_prompt=system,
            model="",
            provider=self._default_provider,
        ):
            yield chunk

    async def stream(
        self,
        messages: list[Message],
        system_prompt: str,
        model: str,
        provider: str,
        max_tokens: int = 8192,
    ) -> AsyncIterator[StreamChunk]:
        current_provider = provider
        attempts = 0

        while True:
            client = self._clients.get(current_provider)
            if client is None:
                raise ProviderError(
                    provider=current_provider,
                    status_code=0,
                    message=f"No client configured for provider: {current_provider}",
                )
            try:
                async for chunk in client.stream(
                    messages, system_prompt, model, max_tokens,
                ):
                    yield chunk
                return

            except ProviderError as e:
                if e.is_rate_limited():
                    next_provider = PROVIDER_FALLBACK_CHAIN.get(current_provider)
                    if next_provider and next_provider in self._clients:
                        logger.warning(
                            "Provider %s rate limited (429), falling back to %s",
                            current_provider,
                            next_provider,
                        )
                        current_provider = next_provider
                        attempts = 0
                        continue
                    raise

                if e.is_retryable() and attempts < MAX_RETRIES:
                    backoff = min(
                        INITIAL_BACKOFF_S * (2**attempts), MAX_BACKOFF_S,
                    )
                    logger.warning(
                        "Provider %s returned %d, retry %d/%d after %.1fs",
                        current_provider,
                        e.status_code,
                        attempts + 1,
                        MAX_RETRIES,
                        backoff,
                    )
                    await asyncio.sleep(backoff)
                    attempts += 1
                    continue
                raise
