"""
ProviderClient — single-provider OpenAI-compatible async HTTP client.
Uses httpx.AsyncClient for all I/O (RULE 2).
Streams SSE tokens incrementally via SseParser.
RULE 9: logs provider name + last 4 chars of API key only.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

from jasusi_cli.api.client import StreamChunk

logger = logging.getLogger(__name__)

# RULE 7: exactly these 7 status codes trigger retry
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 409, 429, 500, 502, 503, 504})
MAX_RETRIES: int = 2
INITIAL_BACKOFF_MS: float = 200.0
MAX_BACKOFF_MS: float = 2000.0


def _redact_key(api_key: str) -> str:
    """Log last 4 chars only — RULE 9."""
    return f"***{api_key[-4:]}" if len(api_key) >= 4 else "***"


@dataclass
class SseParser:
    """
    Incremental SSE byte-stream parser.
    push_chunk() feeds raw bytes; finish() flushes the buffer.
    Yields parsed data payloads as strings.
    """

    _buffer: str = field(default="", init=False)

    def push_chunk(self, chunk: bytes) -> list[str]:
        """Feed a raw byte chunk. Returns list of complete SSE data payloads."""
        self._buffer += chunk.decode("utf-8", errors="replace")
        return self._extract_events()

    def finish(self) -> list[str]:
        """Flush remaining buffer at end of stream."""
        result = self._extract_events()
        self._buffer = ""
        return result

    def _extract_events(self) -> list[str]:
        payloads: list[str] = []
        while "\n\n" in self._buffer:
            event, self._buffer = self._buffer.split("\n\n", 1)
            for line in event.splitlines():
                if line.startswith("data: "):
                    data = line[6:].strip()
                    if data and data != "[DONE]":
                        payloads.append(data)
        return payloads


class ProviderClient:
    """
    Single-provider OpenAI-compatible async client.
    Implements exponential backoff retry for RETRYABLE_STATUS_CODES (RULE 7).
    """

    def __init__(
        self,
        name: str,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
    ) -> None:
        self._name = name
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        logger.debug(
            "ProviderClient: init name=%s model=%s key=%s",
            name, model, _redact_key(api_key),
        )

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[StreamChunk]:
        """Stream completion with retry on retryable status codes."""
        payloads = await self._fetch_sse(messages, tools, system)
        return self._iter_chunks(payloads)

    async def _fetch_sse(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> list[str]:
        """Execute HTTP request with retry, collect all SSE payloads."""
        payload = self._build_payload(messages, tools, system)
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        backoff_ms = INITIAL_BACKOFF_MS
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as http:
                    async with http.stream(
                        "POST",
                        f"{self._base_url}/chat/completions",
                        json=payload,
                        headers=headers,
                    ) as response:
                        if response.status_code in RETRYABLE_STATUS_CODES:
                            if attempt < MAX_RETRIES:
                                wait = min(
                                    backoff_ms / 1000.0, MAX_BACKOFF_MS / 1000.0,
                                )
                                logger.warning(
                                    "ProviderClient: %s HTTP %d — retry %d/%d"
                                    " after %.1fs",
                                    self._name, response.status_code,
                                    attempt + 1, MAX_RETRIES, wait,
                                )
                                await asyncio.sleep(wait)
                                backoff_ms *= 2.0
                                continue
                            raise httpx.HTTPStatusError(
                                f"HTTP {response.status_code} after"
                                f" {MAX_RETRIES} retries",
                                request=response.request,
                                response=response,
                            )
                        response.raise_for_status()

                        parser = SseParser()
                        all_payloads: list[str] = []
                        async for raw_bytes in response.aiter_bytes():
                            all_payloads.extend(parser.push_chunk(raw_bytes))
                        all_payloads.extend(parser.finish())
                        return all_payloads

            except httpx.HTTPStatusError:
                raise
            except Exception as e:
                if attempt < MAX_RETRIES:
                    wait = min(backoff_ms / 1000.0, MAX_BACKOFF_MS / 1000.0)
                    logger.warning(
                        "ProviderClient: %s network error — retry %d/%d"
                        " after %.1fs: %s",
                        self._name, attempt + 1, MAX_RETRIES, wait, e,
                    )
                    await asyncio.sleep(wait)
                    backoff_ms *= 2.0
                else:
                    raise

        raise RuntimeError(f"ProviderClient: {self._name} exhausted retries")

    async def _iter_chunks(
        self, payloads: list[str],
    ) -> AsyncIterator[StreamChunk]:
        """Async generator over parsed SSE payloads."""
        for p in payloads:
            chunk = self._parse_payload(p)
            if chunk is not None:
                yield chunk

    def _parse_payload(self, payload: str) -> StreamChunk | None:
        try:
            data: dict[str, Any] = json.loads(payload)
        except json.JSONDecodeError:
            return None

        choices: list[dict[str, Any]] = data.get("choices", [])
        if not choices:
            usage: dict[str, Any] = data.get("usage", {})
            return StreamChunk(
                delta="",
                input_tokens=int(usage.get("prompt_tokens", 0)),
                output_tokens=int(usage.get("completion_tokens", 0)),
                stop_reason=None,
            )

        choice: dict[str, Any] = choices[0]
        delta: dict[str, Any] = choice.get("delta", {})
        finish_reason: str | None = choice.get("finish_reason")

        # Tool call detection
        tool_calls: list[dict[str, Any]] = delta.get("tool_calls", [])
        if tool_calls:
            tc: dict[str, Any] = tool_calls[0]
            func: dict[str, Any] = tc.get("function", {})
            return StreamChunk(
                delta="",
                tool_name=str(func.get("name", "")),
                tool_input_json=str(func.get("arguments", "{}")).encode(),
                tool_use_id=str(tc.get("id", "")),
                is_tool_call=True,
                input_tokens=0,
                output_tokens=0,
                stop_reason=finish_reason,
            )

        content: str = str(delta.get("content") or "")
        usage = data.get("usage", {})
        return StreamChunk(
            delta=content,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            stop_reason=finish_reason,
        )

    def _build_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> dict[str, Any]:
        all_messages: list[dict[str, Any]] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(messages)

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": all_messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]
            payload["tool_choice"] = "auto"
        return payload
