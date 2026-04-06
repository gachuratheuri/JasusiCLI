"""7-stage bootstrap pipeline — mirrors Claw Code bootstrap_graph.py"""

from __future__ import annotations

import asyncio
import logging
import sys
import uuid
import warnings
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path

from jasusi_cli.config.settings import JasusiSettings, SettingsLoader
from jasusi_cli.memory.session_store import SessionStore
from jasusi_cli.routing.scored_router import ScoredRouter
from jasusi_cli.security.prompt_builder import SystemPromptBuilder

logger = logging.getLogger(__name__)

VALID_EXECUTION_MODES: frozenset[str] = frozenset({"chat", "task", "status", "version"})


class BootstrapPhase(Enum):
    PREFETCH = auto()
    WARNING_HANDLER = auto()
    CLI_PARSER = auto()
    SETUP_AND_COMMANDS = auto()
    DEFERRED_INIT = auto()
    MODE_ROUTING = auto()
    QUERY_ENGINE = auto()
    FAST_PATH_VERSION = auto()
    FAST_PATH_STATUS = auto()
    QUERY_ENGINE_SUBMIT = auto()


class ExecutionMode(Enum):
    LOCAL = "local"
    REMOTE = "remote"
    SIMPLE = "simple"


@dataclass
class BootstrapContext:
    """Immutable snapshot returned from every bootstrap path."""

    phase: BootstrapPhase = BootstrapPhase.PREFETCH
    execution_mode: str = "chat"
    settings: JasusiSettings | None = None
    session_store: SessionStore | None = field(default=None)
    router: ScoredRouter | None = None
    session_id: str | None = None
    task_input: str | None = None
    simple_mode: bool = False
    warnings_attached: bool = False


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

    VERSION: str = "0.1.0"

    def __init__(
        self,
        project_root: Path | None = None,
        *,
        cwd: Path | None = None,
    ) -> None:
        self._project_root = cwd or project_root or Path.cwd()
        self._settings: JasusiSettings | None = None
        self._mode: ExecutionMode = ExecutionMode.LOCAL
        self._session_store: SessionStore | None = None

    # ------------------------------------------------------------------
    # Fast paths — no settings, no async, minimal work
    # ------------------------------------------------------------------

    def run_version_fast_path(self) -> BootstrapContext:
        """Return immediately with version info — no settings loaded."""
        return BootstrapContext(
            phase=BootstrapPhase.FAST_PATH_VERSION,
            execution_mode="version",
        )

    def run_status_fast_path(self) -> BootstrapContext:
        """Load only the session store — skip settings and router."""
        store = SessionStore(base_dir=self._project_root / ".jasusi" / "sessions")
        return BootstrapContext(
            phase=BootstrapPhase.FAST_PATH_STATUS,
            execution_mode="status",
            session_store=store,
        )

    # ------------------------------------------------------------------
    # Full bootstrap path — async
    # ------------------------------------------------------------------

    async def run_full(
        self,
        execution_mode: str = "chat",
        task_input: str | None = None,
        simple_mode: bool = False,
        session_id: str | None = None,
    ) -> BootstrapContext:
        """Run the full 7-phase bootstrap and return a populated context."""
        if execution_mode not in VALID_EXECUTION_MODES:
            logger.warning(
                "Unknown execution_mode=%r, defaulting to 'chat'", execution_mode,
            )
            execution_mode = "chat"

        # Phase 2: Warning handler
        warnings.filterwarnings(
            "ignore", category=DeprecationWarning, module="pkg_resources",
        )

        # Phase 4: Parallel setup
        settings = SettingsLoader.load(self._project_root)
        store = SessionStore(base_dir=self._project_root / ".jasusi" / "sessions")
        router = ScoredRouter()

        sid = session_id or str(uuid.uuid4())[:12]

        return BootstrapContext(
            phase=BootstrapPhase.QUERY_ENGINE_SUBMIT,
            execution_mode=execution_mode,
            settings=settings,
            session_store=store,
            router=router,
            session_id=sid,
            task_input=task_input,
            simple_mode=simple_mode,
            warnings_attached=True,
        )

    # ------------------------------------------------------------------
    # Legacy entry point (phases 6-8 compat)
    # ------------------------------------------------------------------

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
