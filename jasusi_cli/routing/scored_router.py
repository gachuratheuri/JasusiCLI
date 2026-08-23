"""ScoredRouter — provider/model resolution over the canonical route scorer.

This module no longer carries its own keyword bags, thresholds, or model roster.
It previously did, which meant the CLI (``core.router``), the web adapter, and
the bootstrap graph could each classify the same prompt differently and then
dispatch it to a different model. Scoring now lives in :mod:`jasusi_cli.core.router`
and the roster in :mod:`jasusi_cli.config.registry`; this class only maps a route
onto the provider and model that serve it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from jasusi_cli.config.registry import (
    PROVIDER_FALLBACK_CHAIN,
    model_for,
    provider_for,
)
from jasusi_cli.core.router import (
    DEFAULT_ROLE,
    MIN_CONFIDENCE,
    RouteEvaluation,
    evaluate,
)

logger = logging.getLogger(__name__)

# Route targets — kept as named constants for callers and tests.
ROUTE_DEVELOPER: str = "developer"
ROUTE_RESEARCHER: str = "researcher"
ROUTE_ARCHITECT: str = "architect"
ROUTE_REVIEWER: str = "reviewer"
ROUTE_EXECUTOR: str = "executor"

AMBIGUOUS_FALLBACK: str = DEFAULT_ROLE

__all__ = [
    "AMBIGUOUS_FALLBACK",
    "MIN_CONFIDENCE",
    "ROUTE_ARCHITECT",
    "ROUTE_DEVELOPER",
    "ROUTE_EXECUTOR",
    "ROUTE_RESEARCHER",
    "ROUTE_REVIEWER",
    "RouteDecision",
    "ScoredRouter",
]


@dataclass
class RouteDecision:
    route: str
    provider: str
    model: str
    confidence: float
    reason: str
    fallback_provider: str


class ScoredRouter:
    """Resolve a prompt to a route plus the provider/model that serves it.

    Classification is delegated to the canonical scorer so that every entry
    point — CLI, web adapter, bootstrap graph — reaches identical decisions.
    """

    def route(self, query: str, token_count: int = 0) -> RouteDecision:
        evaluation: RouteEvaluation = evaluate(query, token_count)

        if evaluation.below_evidence_floor:
            logger.debug(
                "insufficient evidence (top score %.2f) — falling back to %s",
                evaluation.score,
                AMBIGUOUS_FALLBACK,
            )

        provider = provider_for(evaluation.role)
        decision = RouteDecision(
            route=evaluation.role,
            provider=provider,
            model=model_for(evaluation.role),
            confidence=evaluation.confidence,
            reason=(
                f"score={evaluation.score:.2f}, "
                f"margin={evaluation.margin:.2f}, "
                f"runner_up={evaluation.runner_up}, "
                f"ambiguous={evaluation.ambiguous}"
            ),
            fallback_provider=PROVIDER_FALLBACK_CHAIN.get(provider, provider),
        )
        logger.debug("RouteDecision: %s", decision)
        return decision
