"""Autonomous Agent Runtime for JasusiCLI.

Orchestrates multi-step code exploration, planning, surgical modification, and verification.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from openai import APIStatusError, RateLimitError

from jasusi_cli.agent.context_builder import build_workspace_context
from jasusi_cli.agent.model_selector import get_agent_fallback_chain, select_agent_model
from jasusi_cli.agent.output import AgentOutput, AgentStats
from jasusi_cli.agent.prompt import build_agent_system_prompt
from jasusi_cli.api.client import StreamChunk
from jasusi_cli.core.clients import get_client
from jasusi_cli.integration.wiring import RuntimeConfig, RuntimeFactory
from jasusi_cli.tools.permissions import PermissionPrompter, TerminalPrompter

logger = logging.getLogger(__name__)


class AutoApprovePrompter:
    """Prompter for autonomous execution that approves workspace file modifications

    and prompts only for shell execution if running interactively.
    """

    SAFE_TOOLS: frozenset[str] = frozenset(
        {"file_read", "file_write", "file_edit", "glob_search", "grep_search", "todo_write"}
    )

    def __init__(self, allow_bash: bool = True) -> None:
        self.allow_bash = allow_bash
        self._terminal = TerminalPrompter()

    def ask(self, tool_name: str, command_preview: str) -> bool:
        if tool_name in self.SAFE_TOOLS:
            logger.info("AutoApprove: allowed safe workspace tool=%s", tool_name)
            return True
        if tool_name == "bash" and self.allow_bash:
            logger.info("AutoApprove: allowed bash command preview=%.60s", command_preview)
            return True
        return self._terminal.ask(tool_name, command_preview)


class FallbackAwareAgentClient:
    """Client adapter conforming to ApiClientProtocol that handles multi-model

    fallback across the OpenRouter / GoogleAI chain upon rate-limit / quota errors.
    """

    def __init__(
        self,
        primary_model: str,
        primary_provider: str,
        base_urls: dict[str, str] | None = None,
    ) -> None:
        self.primary_model = primary_model
        self.primary_provider = primary_provider
        self.fallback_chain = get_agent_fallback_chain()
        self.base_urls = base_urls or {
            "openrouter": "https://openrouter.ai/api/v1",
            "google_ai": "https://generativelanguage.googleapis.com/v1beta/openai",
        }

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
    ) -> AsyncIterator[StreamChunk]:
        """Execute a completion with automatic fallback chain execution on 429/quota errors."""
        models_to_try: list[tuple[str, str]] = [(self.primary_model, self.primary_provider)]
        for entry in self.fallback_chain:
            pair = (entry["model"], entry.get("provider", "openrouter"))
            if pair not in models_to_try:
                models_to_try.append(pair)

        last_error: Exception | None = None

        for attempt, (model_id, provider_key) in enumerate(models_to_try):
            try:
                if attempt > 0:
                    wait_time = min(2 ** (attempt - 1), 8)
                    logger.info("Agent: trying fallback %d/%d (%s) after %ds backoff", attempt, len(models_to_try) - 1, model_id, wait_time)
                    await asyncio.sleep(wait_time)

                # Use direct OpenAI SDK client or ProviderClient
                if provider_key == "google_ai":
                    sdk_provider = "googleai"
                else:
                    sdk_provider = provider_key

                client = get_client(sdk_provider)
                payload_messages: list[dict[str, Any]] = []
                if system:
                    payload_messages.append({"role": "system", "content": system})
                payload_messages.extend(messages)

                kwargs: dict[str, Any] = {
                    "model": model_id,
                    "messages": payload_messages,
                    "temperature": 0.1,
                    "max_tokens": 8192,
                }
                if tools:
                    kwargs["tools"] = [{"type": "function", "function": t} for t in tools]
                    kwargs["tool_choice"] = "auto"

                # Synchronous client call executed in worker thread for safety
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: client.chat.completions.create(**kwargs),
                )

                choice = response.choices[0]
                msg = choice.message
                delta_text = msg.content or ""
                input_tokens = getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
                output_tokens = getattr(response.usage, "completion_tokens", 0) if response.usage else 0

                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        yield StreamChunk(
                            delta="",
                            is_tool_call=True,
                            tool_name=tc.function.name,
                            tool_input_json=tc.function.arguments.encode("utf-8"),
                            tool_use_id=tc.id,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                        )
                else:
                    yield StreamChunk(
                        delta=delta_text,
                        is_tool_call=False,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        stop_reason=choice.finish_reason or "stop",
                    )
                return

            except (RateLimitError, APIStatusError) as err:
                status = getattr(err, "status_code", None)
                if status not in (429, 402, 403, 503) and "quota" not in str(err).lower() and "rate" not in str(err).lower():
                    raise
                last_error = err
                logger.warning("Agent model %s rate limited: %s. Walking fallback...", model_id, err)
                continue
            except Exception as other_err:
                logger.warning("Error calling model %s: %s", model_id, other_err)
                last_error = other_err
                continue

        raise RuntimeError(f"All {len(models_to_try)} models in the fallback chain were exhausted. Last error: {last_error}")


async def run_agent(
    task: str,
    *,
    cwd: Path | None = None,
    model: str | None = None,
    permission_mode: str = "workspace-write",
    max_iterations: int = 16,
    session_id: str | None = None,
    output_format: str = "text",
    auto_approve: bool = False,
) -> int:
    """Execute an autonomous agent session for the specified task."""
    workspace_root = (cwd or Path.cwd()).resolve()
    sid = session_id or str(uuid.uuid4())[:12]
    out = AgentOutput(fmt=output_format)

    # 1. Discover workspace context
    context = build_workspace_context(workspace_root)

    # 2. Select model and provider
    model_sel = select_agent_model(preferred_role="developer", force_model=model)
    fallback_chain = get_agent_fallback_chain()

    out.print_header(
        session_id=sid,
        model=model_sel.model_id,
        provider=model_sel.provider,
        workspace_root=workspace_root,
        fallback_count=len(fallback_chain),
    )

    # 3. Build agent system prompt
    system_prompt = build_agent_system_prompt(context)

    # 4. Construct ApiClient with fallback support
    api_client = FallbackAwareAgentClient(
        primary_model=model_sel.model_id,
        primary_provider=model_sel.provider,
    )

    # 5. Build prompter and conversation runtime
    prompter: PermissionPrompter
    if auto_approve or permission_mode in ("danger-full-access", "allow"):
        prompter = AutoApprovePrompter(allow_bash=True)
    else:
        prompter = TerminalPrompter()

    factory = RuntimeFactory(cwd=workspace_root)
    cfg = RuntimeConfig(
        session_id=sid,
        project="agent",
        simple_mode=False,
        cwd=workspace_root,
        max_turns=1,
        max_budget_tokens=16_000,
    )

    runtime, worm, store = factory.build(
        config=cfg,
        api_client=api_client,
        prompter=prompter,
    )
    # Set the generated system prompt directly on the runtime
    runtime._system = system_prompt

    # 6. Execute autonomous turn
    stats = AgentStats(session_id=sid)
    tool_call_count = 0

    try:
        stream = await runtime.submit(task)
        async for chunk in stream:
            if chunk.delta:
                out.print_delta(chunk.delta)
            if chunk.is_tool_call and chunk.tool_name:
                tool_call_count += 1
                preview = chunk.tool_name
                try:
                    import json as _json
                    parsed = _json.loads(chunk.tool_input_json)
                    if "command" in parsed:
                        preview = parsed["command"]
                    elif "path" in parsed or "file_path" in parsed:
                        preview = parsed.get("path") or parsed.get("file_path")
                except Exception:
                    pass
                out.print_tool_call(chunk.tool_name, preview, success=True)

            stats.input_tokens += chunk.input_tokens
            stats.output_tokens += chunk.output_tokens

        stats.tool_calls_count = tool_call_count
        out.print_summary(stats)
        return 0

    except KeyboardInterrupt:
        out.print_error("Execution interrupted by user.")
        return 2
    except Exception as e:
        logger.exception("Agent runtime error: %s", e)
        out.print_error(str(e))
        return 1
