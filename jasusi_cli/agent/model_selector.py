"""Model selection and fallback chain resolution for JasusiCLI Agent."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from jasusi_cli.config.registry import ROLES_BY_NAME
from jasusi_cli.core.clients import get_fallback_chain, get_model

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSelection:
    """Resolved model target and provider for an agent session."""

    model_id: str
    provider: str
    is_fallback: bool = False
    fallback_position: int = 0


def select_agent_model(
    preferred_role: str = "developer",
    force_model: str | None = None,
    force_provider: str | None = None,
) -> ModelSelection:
    """Select the active model for an agent session.

    If force_model is specified:
        - If force_provider is not given, detect provider by model name or default to 'openrouter'.
    Otherwise:
        - Resolve canonical model and provider for the preferred role from settings.
    """
    if force_model:
        model = force_model.strip()
        if force_provider:
            provider = force_provider
        elif model.startswith("gemini"):
            provider = "googleai"
        else:
            provider = "openrouter"
        return ModelSelection(model_id=model, provider=provider, is_fallback=False, fallback_position=0)

    try:
        model_id, provider = get_model(preferred_role)
        return ModelSelection(model_id=model_id, provider=provider, is_fallback=False, fallback_position=0)
    except Exception as e:
        logger.warning("Could not resolve role %s from settings (%s); falling back to registry", preferred_role, e)
        # Use canonical registry as fallback — no hardcoded third roster copy (F11/F14)
        spec = ROLES_BY_NAME.get(preferred_role) or ROLES_BY_NAME["developer"]
        return ModelSelection(
            model_id=spec.model,
            provider=spec.provider_key,
            is_fallback=True,
            fallback_position=0,
        )


def get_agent_fallback_chain() -> list[dict[str, str]]:
    """Return the ordered fallback chain from configuration."""
    try:
        return get_fallback_chain()
    except Exception as e:
        logger.warning("Could not read fallback chain from settings: %s", e)
        # Derive from registry roles rather than maintaining a third roster copy
        return [
            {"model": spec.model, "provider": spec.provider_key}
            for spec in ROLES_BY_NAME.values()
            if spec.provider_key == "openrouter"
        ]

