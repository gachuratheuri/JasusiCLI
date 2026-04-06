"""Per-tool permission policy — Allow | Deny | Prompt.
PermissionPrompter is a Protocol so tests can inject MockPrompter."""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class PermissionMode(Enum):
    ALLOW = auto()
    DENY = auto()
    PROMPT = auto()


@runtime_checkable
class PermissionPrompter(Protocol):
    def ask(self, tool_name: str, command_preview: str) -> bool:
        """Return True to allow, False to deny."""
        ...


class AutoAllowPrompter:
    """Used in tests and non-interactive sessions."""

    def ask(self, tool_name: str, command_preview: str) -> bool:
        logger.debug("AutoAllow: tool=%s preview=%.40s", tool_name, command_preview)
        return True


class AutoDenyPrompter:
    """Used in sandboxed / audit-only sessions."""

    def ask(self, tool_name: str, command_preview: str) -> bool:
        logger.warning("AutoDeny: tool=%s preview=%.40s", tool_name, command_preview)
        return False


class TerminalPrompter:
    """Interactive terminal prompt — asks the user Y/n."""

    def ask(self, tool_name: str, command_preview: str) -> bool:
        try:
            answer = input(
                f"\n[jasusi] Allow tool '{tool_name}'?\n"
                f"  Preview: {command_preview[:120]}\n"
                f"  (Y/n): "
            ).strip().lower()
            return answer in ("y", "yes", "")
        except (EOFError, KeyboardInterrupt):
            return False


class PermissionPolicy:
    """
    BTreeMap-equivalent: tool_name → PermissionMode.
    Default mode for unknown tools: PROMPT.
    """

    DEFAULT_DANGEROUS: frozenset[str] = frozenset(
        {"bash", "file_write", "file_edit"}
    )
    DEFAULT_SAFE: frozenset[str] = frozenset(
        {"file_read", "glob_search", "grep_search", "web_fetch", "web_search", "todo_write"}
    )

    def __init__(
        self,
        prompter: PermissionPrompter | None = None,
        overrides: dict[str, PermissionMode] | None = None,
    ) -> None:
        self._prompter: PermissionPrompter = prompter or TerminalPrompter()
        self._policy: dict[str, PermissionMode] = {}

        # Defaults
        for name in self.DEFAULT_DANGEROUS:
            self._policy[name] = PermissionMode.PROMPT
        for name in self.DEFAULT_SAFE:
            self._policy[name] = PermissionMode.ALLOW

        # Apply overrides
        if overrides:
            self._policy.update(overrides)

    def check(self, tool_name: str, command_preview: str) -> bool:
        """Return True if execution is permitted."""
        mode = self._policy.get(tool_name, PermissionMode.PROMPT)

        if mode == PermissionMode.ALLOW:
            logger.debug("Permission ALLOW: tool=%s", tool_name)
            return True

        if mode == PermissionMode.DENY:
            logger.warning("Permission DENY: tool=%s", tool_name)
            return False

        # PROMPT
        result = self._prompter.ask(tool_name, command_preview)
        logger.info(
            "Permission PROMPT → %s: tool=%s",
            "ALLOW" if result else "DENY",
            tool_name,
        )
        return result

    def set(self, tool_name: str, mode: PermissionMode) -> None:
        self._policy[tool_name] = mode

    def get(self, tool_name: str) -> PermissionMode:
        return self._policy.get(tool_name, PermissionMode.PROMPT)
