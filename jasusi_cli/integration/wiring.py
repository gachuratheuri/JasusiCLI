from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jasusi_cli.config.settings import JasusiSettings, SettingsLoader
from jasusi_cli.core.runtime import ConversationRuntime
from jasusi_cli.integration.worm_ledger import WormLedger
from jasusi_cli.memory.session_store import SessionStore
from jasusi_cli.security.prompt_builder import SystemPromptBuilder
from jasusi_cli.tools.permissions import AutoAllowPrompter, PermissionPrompter

logger = logging.getLogger(__name__)


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
            from jasusi_cli.api.client import ApiClient, MultiProviderClient

            provider_clients: dict[str, ApiClient] = {}
            api_client = MultiProviderClient(provider_clients=provider_clients)

        if tool_executor is None:
            from jasusi_cli.tools.tool_executor import ToolExecutor

            tool_executor = ToolExecutor(
                cwd=self._cwd,
                simple_mode=cfg.simple_mode,
                prompter=prompter or AutoAllowPrompter(),
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
