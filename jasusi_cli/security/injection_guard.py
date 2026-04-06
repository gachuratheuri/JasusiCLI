"""Python injection guard — mirrors Rust security::injection_guard for the Python layer."""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

INJECTION_PATTERNS: tuple[str, ...] = (
    "SYSTEM:",
    "ROUTE:",
    "NO_REPLY",
    "Ignore previous instructions",
    "ignore previous instructions",
    "Disregard all prior",
    "disregard all prior",
    "You are now",
    "Act as if",
    "act as if",
    "Pretend you are",
    "pretend you are",
    "<!-- SYSTEM",
    "<|system|>",
    "<|im_start|>system",
)


@dataclass
class InjectionGuardResult:
    cleaned: str
    stripped_count: int


def clean(text: str) -> InjectionGuardResult:
    """Strip prompt injection patterns line by line."""
    cleaned_lines: list[str] = []
    stripped_count = 0

    for line in text.splitlines():
        trimmed = line.strip()
        is_injection = any(trimmed.startswith(p) for p in INJECTION_PATTERNS)
        if is_injection:
            stripped_count += 1
            logger.warning("Injection pattern stripped: %.60s", trimmed)
        else:
            cleaned_lines.append(line)

    return InjectionGuardResult(
        cleaned="\n".join(cleaned_lines),
        stripped_count=stripped_count,
    )
