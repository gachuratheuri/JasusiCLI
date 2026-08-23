"""Model selection and fallback chain resolution for JasusiCLI Agent."""

from __future__ import annotations

import logging
from dataclasses import dataclass

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
        logger.warning("Could not resolve role %s from settings (%s); falling back to default", preferred_role, e)
        return ModelSelection(
            model_id="qwen/qwen3-coder-480b-a35b:free",
            provider="openrouter",
            is_fallback=True,
            fallback_position=0,
        )


def get_agent_fallback_chain() -> list[dict[str, str]]:
    """Return the ordered fallback chain from configuration."""
    try:
        return get_fallback_chain()
    except Exception as e:
        logger.warning("Could not read fallback chain from settings: %s", e)
        return [
            {"model": "qwen/qwen3-coder-480b-a35b:free", "provider": "openrouter"},
            {"model": "poolside/laguna-s-2.1:free", "provider": "openrouter"},
            {"model": "deepseek/deepseek-r1:free", "provider": "openrouter"},
            {"model": "cohere/north-mini-code:free", "provider": "openrouter"},
            {"model": "nvidia/nemotron-3-ultra-550b-a35b:free", "provider": "openrouter"},
        ]
