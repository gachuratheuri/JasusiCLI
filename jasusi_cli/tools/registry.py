"""Tool registry — validates tool calls against JSON schemas.
Cap: MAX_TOOLS = 15 visible tools per turn (mirrors Claw Code tool_pool.py).
Simple mode: exactly 3 tools."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from jasusi_cli.tools.schema import ToolSpec

logger = logging.getLogger(__name__)

MAX_TOOLS: int = 15
SIMPLE_MODE_TOOLS: frozenset[str] = frozenset({"bash", "file_read", "file_edit"})


class ValidationError(Exception):
    pass


class ToolRegistry:
    def __init__(self, simple_mode: bool = False) -> None:
        self._specs: dict[str, ToolSpec] = {}
        self._simple_mode = simple_mode

    def register(self, spec: ToolSpec) -> None:
        self._specs[spec.name] = spec

    def visible_specs(self) -> list[ToolSpec]:
        """Return at most MAX_TOOLS specs. Simple mode: exactly 3."""
        names = list(self._specs.keys())
        if self._simple_mode:
            names = [n for n in names if n in SIMPLE_MODE_TOOLS]
        return [self._specs[n] for n in names[:MAX_TOOLS]]

    def validate(self, tool_name: str, input_json: bytes) -> dict[str, Any]:
        """
        Validate a tool call against its registered schema.
        RULE 9: logs SHA-256(input_json) only — never the raw bytes.
        Returns parsed dict on success, raises ValidationError on failure.
        """
        input_hash = hashlib.sha256(input_json).hexdigest()
        logger.debug("Validating tool=%s input_hash=%s", tool_name, input_hash)

        if self._simple_mode and tool_name not in SIMPLE_MODE_TOOLS:
            raise ValidationError(
                f"Tool '{tool_name}' not available in simple mode. "
                f"Allowed: {sorted(SIMPLE_MODE_TOOLS)}"
            )

        spec = self._specs.get(tool_name)
        if spec is None:
            raise ValidationError(f"Unknown tool: '{tool_name}'")

        try:
            parsed: dict[str, Any] = json.loads(input_json)
        except json.JSONDecodeError as e:
            raise ValidationError(
                f"Invalid JSON for tool '{tool_name}': {e}"
            ) from e

        if not isinstance(parsed, dict):
            raise ValidationError(
                f"Tool input must be a JSON object, got {type(parsed).__name__}"
            )

        required_params = {p.name for p in spec.parameters if p.required}
        missing = required_params - set(parsed.keys())
        if missing:
            raise ValidationError(
                f"Tool '{tool_name}' missing required parameters: {sorted(missing)}"
            )

        return parsed

    def all_names(self) -> list[str]:
        return list(self._specs.keys())

    def is_registered(self, name: str) -> bool:
        return name in self._specs
