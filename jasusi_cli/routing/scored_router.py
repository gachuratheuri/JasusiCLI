"""ScoredRouter — 5-dimension confidence scoring to select the right model/provider."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Route targets
ROUTE_DEVELOPER: str = "developer"
ROUTE_RESEARCHER: str = "researcher"
ROUTE_ARCHITECT: str = "architect"
ROUTE_REVIEWER: str = "reviewer"
ROUTE_EXECUTOR: str = "executor"

AMBIGUOUS_FALLBACK: str = ROUTE_DEVELOPER
MIN_CONFIDENCE: float = 0.4


@dataclass
class RouteDecision:
    route: str
    provider: str
    model: str
    confidence: float
    reason: str
    fallback_provider: str


# Keywords per route (dimension 1: lexical signal)
ROUTE_KEYWORDS: dict[str, list[str]] = {
    ROUTE_DEVELOPER: [
        "implement", "write", "create", "build", "add", "fix", "debug",
        "function", "class", "method", "refactor", "cargo", "python",
        "rust", "code", "test", "compile", "error",
    ],
    ROUTE_RESEARCHER: [
        "research", "find", "search", "look up", "what is", "explain",
        "how does", "why does", "documentation", "docs", "example",
        "compare", "difference between",
    ],
    ROUTE_ARCHITECT: [
        "design", "architecture", "structure", "system", "pattern",
        "trade-off", "approach", "strategy", "plan", "overview",
        "diagram", "how should", "best way",
    ],
    ROUTE_REVIEWER: [
        "review", "check", "audit", "lint", "validate", "security",
        "performance", "is this correct", "improve", "feedback",
    ],
    ROUTE_EXECUTOR: [
        "run", "execute", "shell", "bash", "command", "script",
        "install", "setup", "deploy", "start", "stop",
    ],
}

# Provider mapping per route
ROUTE_PROVIDER: dict[str, tuple[str, str]] = {
    ROUTE_DEVELOPER:  ("nemotron", "nvidia/llama-3.3-nemotron-super-49b-v1"),
    ROUTE_RESEARCHER: ("gemini",   "gemini-2.5-pro"),
    ROUTE_ARCHITECT:  ("kimi",     "moonshot-v1-128k"),
    ROUTE_REVIEWER:   ("deepseek", "deepseek-reasoner"),
    ROUTE_EXECUTOR:   ("nemotron", "nvidia/llama-3.3-nemotron-super-49b-v1"),
}

PROVIDER_FALLBACK: dict[str, str] = {
    "nemotron": "gemini",
    "gemini":   "kimi",
    "kimi":     "deepseek",
    "deepseek": "kimi",
}


class ScoredRouter:
    """
    Scores a query across 5 dimensions:
    1. Lexical keyword match
    2. Question word detection (what/how/why → researcher)
    3. Code block presence (``` → developer)
    4. Length signal (>200 chars → architect)
    5. Imperative verb at start (run/execute → executor)
    """

    def route(self, query: str) -> RouteDecision:
        scores: dict[str, float] = {r: 0.0 for r in ROUTE_KEYWORDS}
        query_lower = query.lower()

        # Dimension 1: keyword match
        for route_name, keywords in ROUTE_KEYWORDS.items():
            hits = sum(1 for kw in keywords if kw in query_lower)
            scores[route_name] += hits * 0.3

        # Dimension 2: question words
        if re.match(
            r"^(what|how|why|when|where|who|which|explain|describe)",
            query_lower.strip(),
        ):
            scores[ROUTE_RESEARCHER] += 0.5

        # Dimension 3: code block presence
        if "```" in query or "`" in query:
            scores[ROUTE_DEVELOPER] += 0.4

        # Dimension 4: long analytical query
        if len(query) > 200:
            scores[ROUTE_ARCHITECT] += 0.3

        # Dimension 5: imperative verb at start
        if re.match(
            r"^(run|execute|install|start|stop|deploy|setup|bash|shell)",
            query_lower.strip(),
        ):
            scores[ROUTE_EXECUTOR] += 0.6

        best_route = max(scores, key=lambda r: scores[r])
        best_score = scores[best_route]

        # Normalise score to confidence [0, 1]
        total = sum(scores.values()) or 1.0
        confidence = min(best_score / total, 1.0)

        # Low confidence → fall back to developer with warning
        if confidence < MIN_CONFIDENCE:
            logger.warning(
                "ScoredRouter: low confidence %.2f for route %s — defaulting to %s",
                confidence,
                best_route,
                AMBIGUOUS_FALLBACK,
            )
            best_route = AMBIGUOUS_FALLBACK
            confidence = MIN_CONFIDENCE

        provider, model = ROUTE_PROVIDER[best_route]
        fallback = PROVIDER_FALLBACK.get(provider, "gemini")

        decision = RouteDecision(
            route=best_route,
            provider=provider,
            model=model,
            confidence=confidence,
            reason=f"score={best_score:.2f}, top_route={best_route}",
            fallback_provider=fallback,
        )
        logger.debug("RouteDecision: %s", decision)
        return decision
