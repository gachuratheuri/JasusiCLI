"""
ScoredRouter v3 — 6-dimensional intent classifier for JasusiCLI v3.2.
No LLM calls. Pure heuristic scoring. O(n) on query length.
"""

import re
from dataclasses import dataclass, field

ROLES = ["developer", "executor", "architect", "researcher", "reviewer"]

# ── Dimension 1: Lexical keyword bags ────────────────────────────────────────
KEYWORD_SCORES: dict[str, list[tuple[str, float]]] = {
    "developer":  [("implement",0.4),("fix",0.3),("refactor",0.4),
                   ("write",0.3),("build",0.3),("debug",0.4),("patch",0.3),
                   ("function",0.2),("class",0.2),("error",0.2)],
    "executor":   [("bash",0.4),("run",0.3),("deploy",0.4),("execute",0.4),
                   ("install",0.3),("start",0.3),("stop",0.3),("curl",0.4),
                   ("git",0.3),("orchestrate",0.4),("pipeline",0.2)],
    "architect":  [("architecture",0.4),("design",0.3),("pattern",0.3),
                   ("trade-off",0.4),("tradeoff",0.4),("structure",0.3),
                   ("scaffold",0.3),("schema",0.3),("system",0.2)],
    "researcher": [("docs",0.4),("explain",0.3),("guide",0.3),
                   ("documentation",0.4),("research",0.4),("reference",0.3)],
    "reviewer":   [("review",0.4),("audit",0.4),("security",0.3),
                   ("lint",0.4),("scan",0.3),("vulnerability",0.4),
                   ("check",0.2),("validate",0.3)],
}

# ── Dimension 2: Question-start tokens → Researcher ─────────────────────────
QUESTION_STARTS = {"what", "why", "how", "where", "explain", "docs"}

# ── Dimension 5: Imperative verbs → Executor (highest weight: +0.6) ─────────
IMPERATIVE_VERBS = {
    "run","deploy","bash","execute","install","start",
    "stop","curl","git","orchestrate"
}

# ── Dimension 6: Semantic complexity conjunctions ────────────────────────────
COMPLEXITY_PATTERNS = [
    r",\s*then\b", r"\band also\b", r"\bbut first\b", r"\bafter that\b",
    r"\bif .{1,30} fails\b", r"\bunless\b", r"\bwhen complete\b",
]


# ── Calibration constants ────────────────────────────────────────────────────

#: Safety-ordered preference used to break near-ties deterministically.
TIE_BREAK_ORDER = ["executor", "developer", "architect", "researcher", "reviewer"]

#: Score gap below which the top two routes are treated as indistinguishable.
AMBIGUITY_MARGIN = 0.05

#: Minimum evidence required to accept a classification at all.
CONFIDENCE_THRESHOLD = 0.45

#: Deterministic fallback when evidence is insufficient.
DEFAULT_ROLE = "developer"

#: Token pressure at which routing yields to compaction.
COMPACTION_TOKEN_THRESHOLD = 50_000


@dataclass
class RouteScore:
    role: str
    score: float
    dimensions: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RouteEvaluation:
    """A route decision together with the signals that justify it.

    ``margin`` and ``ambiguous`` expose calibration rather than a raw score
    ratio, so callers can distinguish "confidently developer" from "developer
    because nothing else scored".
    """

    role: str
    score: float
    runner_up: str
    runner_up_score: float
    margin: float
    ambiguous: bool
    below_evidence_floor: bool
    dimensions: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def confidence(self) -> float:
        """Calibrated confidence in [0, 1].

        Derived from the margin over the runner-up, not from the winner's share
        of the total score: a large absolute score means little when a second
        route scored almost as high.
        """
        if self.below_evidence_floor:
            return MIN_CONFIDENCE
        return max(MIN_CONFIDENCE, min(1.0, MIN_CONFIDENCE + self.margin))


#: Floor reported for fallback decisions, so callers never see a bare 0.0.
MIN_CONFIDENCE = 0.4


def evaluate_query(query: str, token_count: int = 0) -> RouteEvaluation:
    """
    Score a query across six heuristic dimensions.
    Compaction check is handled by :func:`evaluate` before calling this.
    """
    q = query.strip()
    q_lower = q.lower()
    first_token = q_lower.split()[0] if q_lower.split() else ""
    scores: dict[str, float] = {r: 0.0 for r in ROLES}
    dims: dict[str, dict[str, object]] = {r: {} for r in ROLES}

    # ── Dim 1: Lexical match (max +0.5 per role) ─────────────────────────────
    for role, pairs in KEYWORD_SCORES.items():
        total = 0.0
        for kw, weight in pairs:
            if kw in q_lower:
                total = min(total + weight, 0.5)
        scores[role] += total
        dims[role]["lexical"] = round(total, 3)

    # ── Dim 2: Question heuristic → Researcher (+0.5) ────────────────────────
    if first_token in QUESTION_STARTS:
        scores["researcher"] += 0.5
        dims["researcher"]["question_heuristic"] = 0.5

    # ── Dim 3: Markdown artifact → Developer (+0.4) ──────────────────────────
    if "```" in q:
        scores["developer"] += 0.4
        dims["developer"]["markdown_artifact"] = 0.4

    # ── Dim 4: Length velocity → Architect ───────────────────────────────────
    char_count = len(q)
    if char_count > 400:
        scores["architect"] += 0.4
        scores["developer"] = max(0.0, scores["developer"] - 0.1)
        dims["architect"]["length_velocity"] = 0.4
    elif char_count > 200:
        scores["architect"] += 0.3
        dims["architect"]["length_velocity"] = 0.3

    # ── Dim 5: Imperative verb → Executor (+0.6, highest weight) ─────────────
    if first_token in IMPERATIVE_VERBS:
        scores["executor"] += 0.6
        dims["executor"]["imperative_verb"] = 0.6

    # ── Dim 6: Semantic complexity → Architect / Developer split ─────────────
    clause_count = sum(
        1 for p in COMPLEXITY_PATTERNS if re.search(p, q_lower)
    )
    if clause_count >= 4:
        scores["architect"] += 0.4
        scores["developer"] = max(0.0, scores["developer"] - 0.2)
        dims["architect"]["semantic_complexity"] = 0.4
    elif clause_count >= 2:
        scores["architect"] += 0.2
        dims["architect"]["semantic_complexity"] = 0.2

    # ── Tie-break: within 0.05 → prefer by safety hierarchy ─────────────────
    ranked = sorted(
        scores.items(),
        key=lambda x: (-x[1], TIE_BREAK_ORDER.index(x[0])),
    )
    best_role, best_score = ranked[0]
    second_role, second_score = ranked[1]

    ambiguous = (best_score - second_score) <= AMBIGUITY_MARGIN
    if ambiguous:
        for preferred in TIE_BREAK_ORDER:
            if preferred in (best_role, second_role):
                best_role = preferred
                break

    below_evidence_floor = best_score < CONFIDENCE_THRESHOLD
    if below_evidence_floor:
        best_role = DEFAULT_ROLE

    return RouteEvaluation(
        role=best_role,
        score=best_score,
        runner_up=second_role,
        runner_up_score=second_score,
        margin=best_score - second_score,
        ambiguous=ambiguous,
        below_evidence_floor=below_evidence_floor,
        dimensions=dims,
    )


def score_query(query: str, token_count: int = 0) -> str:
    """Return the winning role name for a query."""
    return evaluate_query(query, token_count).role


def route(query: str, token_count: int = 0) -> str:
    """Public entry point. Returns role name string."""
    return evaluate(query, token_count).role


def evaluate(query: str, token_count: int = 0) -> RouteEvaluation:
    """Full route decision including calibration signals.

    This is the single scoring implementation for the Python side. Both
    ``core.router`` callers and ``routing.scored_router`` resolve through it, so
    the CLI and the web adapter cannot reach different conclusions for the same
    prompt.
    """
    if token_count >= COMPACTION_TOKEN_THRESHOLD:
        return RouteEvaluation(
            role="compaction",
            score=1.0,
            runner_up=DEFAULT_ROLE,
            runner_up_score=0.0,
            margin=1.0,
            ambiguous=False,
            below_evidence_floor=False,
            dimensions={},
        )
    return evaluate_query(query, token_count)
