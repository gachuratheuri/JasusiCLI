use regex::Regex;
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum AgentRole {
    Developer,
    Executor,
    Architect,
    Researcher,
    Reviewer,
    Compaction,
}

impl AgentRole {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Developer => "developer",
            Self::Executor => "executor",
            Self::Architect => "architect",
            Self::Researcher => "researcher",
            Self::Reviewer => "reviewer",
            Self::Compaction => "compaction",
        }
    }

    #[must_use]
    pub fn parse(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "developer" | "dev" => Some(Self::Developer),
            "executor" | "exec" => Some(Self::Executor),
            "architect" | "arch" => Some(Self::Architect),
            "researcher" | "research" => Some(Self::Researcher),
            "reviewer" | "review" => Some(Self::Reviewer),
            "compaction" | "compact" => Some(Self::Compaction),
            _ => None,
        }
    }
}

pub const ROLES: [AgentRole; 5] = [
    AgentRole::Developer,
    AgentRole::Executor,
    AgentRole::Architect,
    AgentRole::Researcher,
    AgentRole::Reviewer,
];

pub const TIE_BREAK_ORDER: [AgentRole; 5] = [
    AgentRole::Executor,
    AgentRole::Developer,
    AgentRole::Architect,
    AgentRole::Researcher,
    AgentRole::Reviewer,
];

fn keyword_scores() -> &'static [(AgentRole, &'static [(&'static str, f64)])] {
    &[
        (
            AgentRole::Developer,
            &[
                ("implement", 0.4),
                ("fix", 0.3),
                ("refactor", 0.4),
                ("write", 0.3),
                ("build", 0.3),
                ("debug", 0.4),
                ("patch", 0.3),
                ("function", 0.2),
                ("class", 0.2),
                ("error", 0.2),
            ],
        ),
        (
            AgentRole::Executor,
            &[
                ("bash", 0.4),
                ("run", 0.3),
                ("deploy", 0.4),
                ("execute", 0.4),
                ("install", 0.3),
                ("start", 0.3),
                ("stop", 0.3),
                ("curl", 0.4),
                ("git", 0.3),
                ("orchestrate", 0.4),
                ("pipeline", 0.2),
            ],
        ),
        (
            AgentRole::Architect,
            &[
                ("architecture", 0.4),
                ("design", 0.3),
                ("pattern", 0.3),
                ("trade-off", 0.4),
                ("tradeoff", 0.4),
                ("structure", 0.3),
                ("scaffold", 0.3),
                ("schema", 0.3),
                ("system", 0.2),
            ],
        ),
        (
            AgentRole::Researcher,
            &[
                ("docs", 0.4),
                ("explain", 0.3),
                ("guide", 0.3),
                ("documentation", 0.4),
                ("research", 0.4),
                ("reference", 0.3),
            ],
        ),
        (
            AgentRole::Reviewer,
            &[
                ("review", 0.4),
                ("audit", 0.4),
                ("security", 0.3),
                ("lint", 0.4),
                ("scan", 0.3),
                ("vulnerability", 0.4),
                ("check", 0.2),
                ("validate", 0.3),
            ],
        ),
    ]
}

const QUESTION_STARTS: [&str; 6] = ["what", "why", "how", "where", "explain", "docs"];

const IMPERATIVE_VERBS: [&str; 10] = [
    "run",
    "deploy",
    "bash",
    "execute",
    "install",
    "start",
    "stop",
    "curl",
    "git",
    "orchestrate",
];

const COMPLEXITY_PATTERNS: [&str; 7] = [
    r",\s*then\b",
    r"\band also\b",
    r"\bbut first\b",
    r"\bafter that\b",
    r"\bif .{1,30} fails\b",
    r"\bunless\b",
    r"\bwhen complete\b",
];

#[derive(Debug, Clone)]
pub struct RouteResult {
    pub winning_role: AgentRole,
    pub score: f64,
    pub scores: BTreeMap<String, f64>,
}

const CONFIDENCE_THRESHOLD: f64 = 0.45;

#[must_use]
#[allow(clippy::too_many_lines)]
pub fn score_query(query: &str) -> RouteResult {
    let q = query.trim();
    let q_lower = q.to_lowercase();
    let first_token = q_lower.split_whitespace().next().unwrap_or("");

    let mut scores: BTreeMap<AgentRole, f64> = ROLES.iter().map(|&r| (r, 0.0)).collect();

    // Dim 1: Lexical match (max +0.5 per role)
    for &(role, pairs) in keyword_scores() {
        let mut total = 0.0;
        for &(kw, weight) in pairs {
            if q_lower.contains(kw) {
                total = (total + weight).min(0.5);
            }
        }
        if let Some(s) = scores.get_mut(&role) {
            *s += total;
        }
    }

    // Dim 2: Question start token -> Researcher (+0.5)
    if QUESTION_STARTS.contains(&first_token) {
        if let Some(s) = scores.get_mut(&AgentRole::Researcher) {
            *s += 0.5;
        }
    }

    // Dim 3: Markdown artifact -> Developer (+0.4)
    if q.contains("```") {
        if let Some(s) = scores.get_mut(&AgentRole::Developer) {
            *s += 0.4;
        }
    }

    // Dim 4: Length velocity -> Architect (+0.3 or +0.4, Dev penalty)
    let char_count = q.chars().count();
    if char_count > 400 {
        if let Some(s) = scores.get_mut(&AgentRole::Architect) {
            *s += 0.4;
        }
        if let Some(s) = scores.get_mut(&AgentRole::Developer) {
            *s = (*s - 0.1).max(0.0);
        }
    } else if char_count > 200 {
        if let Some(s) = scores.get_mut(&AgentRole::Architect) {
            *s += 0.3;
        }
    }

    // Dim 5: Imperative verb -> Executor (+0.6)
    if IMPERATIVE_VERBS.contains(&first_token) {
        if let Some(s) = scores.get_mut(&AgentRole::Executor) {
            *s += 0.6;
        }
    }

    // Dim 6: Semantic complexity -> Architect (+0.2 or +0.4, Dev penalty)
    let mut clause_count = 0;
    for pat in &COMPLEXITY_PATTERNS {
        if let Ok(re) = Regex::new(pat) {
            if re.is_match(&q_lower) {
                clause_count += 1;
            }
        }
    }

    if clause_count >= 4 {
        if let Some(s) = scores.get_mut(&AgentRole::Architect) {
            *s += 0.4;
        }
        if let Some(s) = scores.get_mut(&AgentRole::Developer) {
            *s = (*s - 0.2).max(0.0);
        }
    } else if clause_count >= 2 {
        if let Some(s) = scores.get_mut(&AgentRole::Architect) {
            *s += 0.2;
        }
    }

    // Convert BTreeMap to sorted Vec by score desc, tie-break hierarchy asc
    let mut ranked: Vec<(AgentRole, f64)> = scores.into_iter().collect();
    ranked.sort_by(|a, b| {
        b.1.partial_cmp(&a.1)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                let pos_a = TIE_BREAK_ORDER
                    .iter()
                    .position(|&r| r == a.0)
                    .unwrap_or(usize::MAX);
                let pos_b = TIE_BREAK_ORDER
                    .iter()
                    .position(|&r| r == b.0)
                    .unwrap_or(usize::MAX);
                pos_a.cmp(&pos_b)
            })
    });

    let (mut best_role, best_score) = ranked[0];
    let (_, second_score) = ranked[1];

    // Tie-break: within 0.05 -> prefer by safety hierarchy
    if (best_score - second_score) <= 0.05 {
        for &preferred in &TIE_BREAK_ORDER {
            if ranked.iter().take(2).any(|&(r, _)| r == preferred) {
                best_role = preferred;
                break;
            }
        }
    }

    // Confidence floor: below 0.45 -> default Developer
    let winning_role = if best_score < CONFIDENCE_THRESHOLD {
        AgentRole::Developer
    } else {
        best_role
    };

    let scores_map = ranked
        .into_iter()
        .map(|(r, s)| (r.as_str().to_string(), s))
        .collect();

    RouteResult {
        winning_role,
        score: best_score,
        scores: scores_map,
    }
}

#[must_use]
pub fn route(query: &str, token_count: usize) -> AgentRole {
    if token_count >= 50_000 {
        return AgentRole::Compaction;
    }
    score_query(query).winning_role
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routes_compaction_on_large_token_count() {
        assert_eq!(route("fix bug", 50_000), AgentRole::Compaction);
        assert_eq!(route("fix bug", 60_000), AgentRole::Compaction);
    }

    #[test]
    fn routes_executor_on_imperative_verbs() {
        assert_eq!(route("run cargo test", 100), AgentRole::Executor);
        assert_eq!(
            route("deploy the application to staging", 100),
            AgentRole::Executor
        );
        assert_eq!(route("bash ls -la", 100), AgentRole::Executor);
    }

    #[test]
    fn routes_researcher_on_questions() {
        assert_eq!(
            route("what is the purpose of this file?", 100),
            AgentRole::Researcher
        );
        assert_eq!(
            route("explain how authentication works", 100),
            AgentRole::Researcher
        );
    }

    #[test]
    fn routes_developer_on_code_modifications() {
        assert_eq!(
            route(
                "implement a new feature in rust with function and class",
                100
            ),
            AgentRole::Developer
        );
        assert_eq!(
            route("fix the bug in function process_data", 100),
            AgentRole::Developer
        );
    }

    #[test]
    fn routes_reviewer_on_audit_query() {
        assert_eq!(
            route(
                "review and audit security vulnerability in the codebase",
                100
            ),
            AgentRole::Reviewer
        );
    }

    #[test]
    fn routes_architect_on_large_query_and_complexity() {
        let long_query = "architecture design pattern system structure ".repeat(15);
        assert_eq!(route(&long_query, 100), AgentRole::Architect);
    }

    #[test]
    fn defaults_to_developer_on_low_confidence() {
        assert_eq!(route("xyz", 100), AgentRole::Developer);
    }
}
