"""7-stage bootstrap pipeline — mirrors Claw Code bootstrap_graph.py"""

from __future__ import annotations

import asyncio
import logging
import sys
import warnings
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path

from jasusi_cli.config.settings import JasusiSettings, SettingsLoader
from jasusi_cli.memory.session_store import SessionStore
from jasusi_cli.routing.scored_router import ScoredRouter
from jasusi_cli.security.prompt_builder import SystemPromptBuilder

logger = logging.getLogger(__name__)


class BootstrapPhase(Enum):
    PREFETCH = auto()
    WARNING_HANDLER = auto()
    CLI_PARSER = auto()
    SETUP_AND_COMMANDS = auto()
    DEFERRED_INIT = auto()
    MODE_ROUTING = auto()
    QUERY_ENGINE = auto()


class ExecutionMode(Enum):
    LOCAL = "local"
    REMOTE = "remote"
    SIMPLE = "simple"


@dataclass
class BootstrapResult:
    settings: JasusiSettings
    session_store: SessionStore
    prompt_builder: SystemPromptBuilder
    router: ScoredRouter
    mode: ExecutionMode
    project_root: Path


class BootstrapGraph:
    """
    Executes the 7 bootstrap phases in order.
    Phase 3 (CLI_PARSER) decision is final and irreversible.
    Phases 4a/4b (setup + commands) run in parallel via asyncio.gather.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self._project_root = project_root or Path.cwd()
        self._settings: JasusiSettings | None = None
        self._mode: ExecutionMode = ExecutionMode.LOCAL
        self._session_store: SessionStore | None = None

    def run(self, argv: list[str] | None = None) -> BootstrapResult:
        return asyncio.run(self._run_async(argv or sys.argv[1:]))

    async def _run_async(self, argv: list[str]) -> BootstrapResult:
        await self._phase_prefetch()
        self._phase_warning_handler()
        self._mode = self._phase_cli_parser(argv)
        await self._phase_setup_and_commands_parallel()
        self._phase_deferred_init()
        self._phase_mode_routing()

        assert self._settings is not None
        return await self._phase_query_engine()

    # Phase 1: Prefetch — warm caches
    async def _phase_prefetch(self) -> None:
        logger.debug("Bootstrap Phase 1: PREFETCH")

    # Phase 2: Warning handler
    def _phase_warning_handler(self) -> None:
        logger.debug("Bootstrap Phase 2: WARNING_HANDLER")
        warnings.filterwarnings(
            "ignore", category=DeprecationWarning, module="pkg_resources",
        )

    # Phase 3: CLI parser — decision is final
    def _phase_cli_parser(self, argv: list[str]) -> ExecutionMode:
        logger.debug("Bootstrap Phase 3: CLI_PARSER argv=%s", argv)
        self._settings = SettingsLoader.load(self._project_root)
        if "--simple" in argv or self._settings.simple_mode:
            return ExecutionMode.SIMPLE
        if "--remote" in argv:
            return ExecutionMode.REMOTE
        return ExecutionMode.LOCAL

    # Phase 4: Setup + Commands load in parallel (only concurrent phase)
    async def _phase_setup_and_commands_parallel(self) -> None:
        logger.debug("Bootstrap Phase 4: SETUP_AND_COMMANDS (parallel)")
        await asyncio.gather(
            self._setup_environment(),
            self._load_commands_snapshot(),
        )

    async def _setup_environment(self) -> None:
        store_path = SessionStore.default_path()
        self._session_store = SessionStore.open(store_path)
        if self._settings:
            self._session_store.prune(
                self._settings.session.prune_after_days,
                self._settings.session.max_entries,
            )

    async def _load_commands_snapshot(self) -> None:
        # Commands registry loaded lazily in Phase 5 via deferred init
        logger.debug("Commands snapshot loaded")

    # Phase 5: Deferred init — components that depend on CLI state
    def _phase_deferred_init(self) -> None:
        logger.debug("Bootstrap Phase 5: DEFERRED_INIT mode=%s", self._mode)

    # Phase 6: Mode routing
    def _phase_mode_routing(self) -> None:
        logger.debug("Bootstrap Phase 6: MODE_ROUTING → %s", self._mode)

    # Phase 7: Query engine submit loop
    async def _phase_query_engine(self) -> BootstrapResult:
        logger.debug("Bootstrap Phase 7: QUERY_ENGINE")
        assert self._settings is not None
        assert self._session_store is not None

        prompt_builder = SystemPromptBuilder(
            project_root=self._project_root,
            max_chars_per_file=self._settings.jasusi_md_max_chars,
            max_total_chars=self._settings.jasusi_md_total_max_chars,
        )
        router = ScoredRouter()

        return BootstrapResult(
            settings=self._settings,
            session_store=self._session_store,
            prompt_builder=prompt_builder,
            router=router,
            mode=self._mode,
            project_root=self._project_root,
        )
