"""Canonical role, model, and version registry (F11, F14).

Single source of truth. Before this module existed the repository carried four
divergent model rosters (README, ``app.py`` status endpoint, ``main.py`` status
printer, and ``routing.scored_router``) and five different version strings.

Anything that names a model, a provider, or the product version must render from
here. Adding a role in one place and not another is what produced the original
divergence, so the registry is deliberately the only place these literals live.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata

# ── Version ──────────────────────────────────────────────────────────────────

_FALLBACK_VERSION = "3.0.0"


def _resolve_version() -> str:
    """Resolve the installed distribution version, falling back for source runs."""
    try:
        return metadata.version("jasusi-cli")
    except metadata.PackageNotFoundError:
        return _FALLBACK_VERSION


VERSION: str = _resolve_version()


# ── Roles ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RoleSpec:
    """A routable specialist role and the provider/model that serves it."""

    role: str
    model: str
    provider: str
    provider_key: str
    #: Requests-per-day ceiling used for quota display. ``None`` means unmetered.
    daily_request_limit: int | None = None


#: Provider key → human-readable provider name.
PROVIDERS: dict[str, str] = {
    "openrouter": "OpenRouter",
    "google_ai": "Google AI Studio",
}

#: Provider key → environment variable holding its credential.
PROVIDER_ENV_VARS: dict[str, str] = {
    "openrouter": "OPENROUTER_API_KEY",
    "google_ai": "GOOGLE_AI_STUDIO_KEY",
}

#: The canonical roster. Order is display order.
ROLES: tuple[RoleSpec, ...] = (
    RoleSpec("developer", "gemini-2.5-flash", "Google AI Studio", "google_ai", 500),
    RoleSpec(
        "executor",
        "nvidia/nemotron-3-ultra-550b-a35b:free",
        "OpenRouter",
        "openrouter",
    ),
    RoleSpec("architect", "moonshotai/kimi-k2.5", "OpenRouter", "openrouter"),
    RoleSpec("researcher", "gemini-2.5-pro", "Google AI Studio", "google_ai", 100),
    RoleSpec("reviewer", "deepseek/deepseek-v3.2", "OpenRouter", "openrouter"),
    RoleSpec(
        "compaction",
        "gemini-2.5-flash-lite",
        "Google AI Studio",
        "google_ai",
        1000,
    ),
)

ROLES_BY_NAME: dict[str, RoleSpec] = {spec.role: spec for spec in ROLES}

#: Roles the router may select. ``compaction`` is triggered by token pressure,
#: not by intent classification, so it is not a routing target.
ROUTABLE_ROLES: tuple[str, ...] = tuple(
    spec.role for spec in ROLES if spec.role != "compaction"
)

#: Provider degradation order used when a provider returns 429.
PROVIDER_FALLBACK_CHAIN: dict[str, str] = {
    "openrouter": "google_ai",
    "google_ai": "openrouter",
}


def model_for(role: str) -> str:
    """Return the canonical model id for a role."""
    return ROLES_BY_NAME[role].model


def provider_for(role: str) -> str:
    """Return the canonical provider key for a role."""
    return ROLES_BY_NAME[role].provider_key


def roster() -> list[dict[str, str]]:
    """Render the roster for status endpoints and CLI status output."""
    return [
        {"role": spec.role.capitalize(), "model": spec.model, "provider": spec.provider}
        for spec in ROLES
    ]


__all__ = [
    "PROVIDERS",
    "PROVIDER_ENV_VARS",
    "PROVIDER_FALLBACK_CHAIN",
    "ROLES",
    "ROLES_BY_NAME",
    "ROUTABLE_ROLES",
    "VERSION",
    "RoleSpec",
    "model_for",
    "provider_for",
    "roster",
]
