from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jasusi_cli.config.settings import JasusiSettings, SettingsLoader
from jasusi_cli.core.runtime import ConversationRuntime
from jasusi_cli.integration.worm_ledger import WormLedger
from jasusi_cli.memory.session_store import SessionStore
from jasusi_cli.security.prompt_builder import SystemPromptBuilder
from jasusi_cli.tools.permissions import PermissionPrompter, TerminalPrompter

logger = logging.getLogger(__name__)


class ProviderConfigurationError(RuntimeError):
    """No usable provider is configured for the requested roles."""


def build_provider_client(base_urls: dict[str, str] | None = None) -> Any:
    """Construct a provider client from validated, enabled providers.

    Only providers whose credential is actually present are registered. If none
    is available the runtime fails at construction with an explanatory error,
    rather than building an empty client that fails on the first turn with an
    opaque ``AttributeError``/``KeyError``.
    """
    from jasusi_cli.api.client import ApiClient, MultiProviderClient
    from jasusi_cli.api.provider_client import ProviderClient
    from jasusi_cli.config.registry import (
        PROVIDER_ENV_VARS,
        model_for,
        provider_for,
    )

    endpoints = base_urls or {
        "openrouter": "https://openrouter.ai/api/v1",
        "google_ai": "https://generativelanguage.googleapis.com/v1beta/openai",
    }

    clients: dict[str, ApiClient] = {}
    missing: list[str] = []
    for provider_key, env_var in PROVIDER_ENV_VARS.items():
        api_key = os.environ.get(env_var, "").strip()
        if not api_key:
            missing.append(env_var)
            continue
        clients[provider_key] = ProviderClient(  # type: ignore[assignment]
            name=provider_key,
            api_key=api_key,
            base_url=endpoints[provider_key],
            model=model_for("developer"),
        )

    if not clients:
        raise ProviderConfigurationError(
            "no provider credentials configured; set one of: "
            + ", ".join(sorted(missing)),
        )

    default_provider = provider_for("developer")
    if default_provider not in clients:
        # The role's preferred provider is unavailable; degrade explicitly and
        # loudly rather than silently routing to an unintended provider.
        fallback = next(iter(clients))
        logger.warning(
            "provider %s unavailable for the default role; using %s",
            default_provider, fallback,
        )
        default_provider = fallback

    return MultiProviderClient(
        provider_clients=clients, default_provider=default_provider,
    )


@dataclass
class RuntimeConfig:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    project: str = "default"
    simple_mode: bool = False
    cwd: Path = field(default_factory=Path.cwd)
    memory_dir: str = ".jasusi/memory"
    max_turns: int = 8
    max_budget_tokens: int = 2_000
    compact_after_turns: int = 12
    task_input: str = ""


class RuntimeFactory:
    """
    Builds a ConversationRuntime from config.
    Accepts optional injectable api_client and tool_executor for testing.
    """

    def __init__(
        self,
        settings: JasusiSettings | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._settings = settings or SettingsLoader.load(cwd)
        self._cwd = cwd or Path.cwd()

    def build(
        self,
        config: RuntimeConfig | None = None,
        api_client: Any | None = None,
        tool_executor: Any | None = None,
        prompter: PermissionPrompter | None = None,
    ) -> tuple[ConversationRuntime[Any, Any], WormLedger, SessionStore]:
        cfg = config or RuntimeConfig()

        worm = WormLedger(persist_dir=str(self._cwd / cfg.memory_dir))
        store = SessionStore(base_dir=self._cwd / ".jasusi" / "sessions")

        existing = store.get_session(cfg.session_id)
        if existing is None:
            store.create_session(cfg.session_id, project=cfg.project)

        prompt_builder = SystemPromptBuilder(project_root=self._cwd)
        system_prompt = prompt_builder.build_turn()

        if api_client is None:
            api_client = build_provider_client()

        if tool_executor is None:
            from jasusi_cli.tools.tool_executor import ToolExecutor

            tool_executor = ToolExecutor(
                cwd=self._cwd,
                simple_mode=cfg.simple_mode,
                # Never auto-allow by default. TerminalPrompter asks an
                # interactive user and denies outright when stdin is not a TTY,
                # so an unattended run cannot silently grant tool access.
                prompter=prompter or TerminalPrompter(),
            )

        runtime: ConversationRuntime[Any, Any] = ConversationRuntime(
            api_client=api_client,
            tool_executor=tool_executor,
            session_id=cfg.session_id,
            system_prompt=system_prompt,
            max_turns=cfg.max_turns,
            max_budget_tokens=cfg.max_budget_tokens,
            compact_after_turns=cfg.compact_after_turns,
        )

        logger.info(
            "RuntimeFactory: built runtime session=%s project=%s simple=%s",
            cfg.session_id, cfg.project, cfg.simple_mode,
        )
        return runtime, worm, store
