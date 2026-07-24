use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

/// Scope at which a quota is enforced.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum QuotaScope {
    Global,
    User,
    Project,
    Provider,
    Tool,
    UserProject,
    UserProvider,
    UserTool,
    ProjectProvider,
    ProjectTool,
}

impl QuotaScope {
    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Global => "global",
            Self::User => "user",
            Self::Project => "project",
            Self::Provider => "provider",
            Self::Tool => "tool",
            Self::UserProject => "user_project",
            Self::UserProvider => "user_provider",
            Self::UserTool => "user_tool",
            Self::ProjectProvider => "project_provider",
            Self::ProjectTool => "project_tool",
        }
    }
}

/// Limit configuration for a single scope.
#[derive(Debug, Clone, Copy)]
pub struct QuotaLimit {
    pub requests_per_minute: Option<u64>,
    pub requests_per_hour: Option<u64>,
    pub requests_per_day: Option<u64>,
    pub max_concurrent: Option<u64>,
    pub input_tokens_per_hour: Option<u64>,
    pub output_tokens_per_hour: Option<u64>,
}

impl QuotaLimit {
    #[must_use]
    pub const fn unlimited() -> Self {
        Self {
            requests_per_minute: None,
            requests_per_hour: None,
            requests_per_day: None,
            max_concurrent: None,
            input_tokens_per_hour: None,
            output_tokens_per_hour: None,
        }
    }

    #[must_use]
    pub const fn default_global() -> Self {
        Self {
            requests_per_minute: Some(60),
            requests_per_hour: Some(600),
            requests_per_day: Some(5_000),
            max_concurrent: Some(4),
            input_tokens_per_hour: None,
            output_tokens_per_hour: None,
        }
    }
}

/// A request for quota admission.
#[derive(Debug, Clone, Default)]
pub struct QuotaRequest<'a> {
    pub user_id: Option<&'a str>,
    pub project_id: Option<&'a str>,
    pub provider: Option<&'a str>,
    pub tool: Option<&'a str>,
    pub input_tokens: u64,
    pub output_tokens: u64,
}

/// Verdict returned by the quota manager.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct QuotaVerdict {
    pub allowed: bool,
    pub remaining: u64,
    pub limit: u64,
    pub scope: &'static str,
    pub reason: String,
}

impl QuotaVerdict {
    #[must_use]
    fn allowed(limit: u64, remaining: u64, scope: &'static str, reason: impl Into<String>) -> Self {
        Self {
            allowed: true,
            remaining,
            limit,
            scope,
            reason: reason.into(),
        }
    }

    #[must_use]
    fn denied(limit: u64, remaining: u64, scope: &'static str, reason: impl Into<String>) -> Self {
        Self {
            allowed: false,
            remaining,
            limit,
            scope,
            reason: reason.into(),
        }
    }
}

/// Token bucket with minute/hour/day sliding windows and concurrency accounting.
#[derive(Debug, Default)]
struct WindowCounters {
    minute_start: AtomicU64,
    minute_count: AtomicU64,
    hour_start: AtomicU64,
    hour_count: AtomicU64,
    day_start: AtomicU64,
    day_count: AtomicU64,
    input_tokens_hour: AtomicU64,
    output_tokens_hour: AtomicU64,
    concurrent: AtomicU64,
}

/// Composite key for a quota bucket.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct QuotaKey(String);

impl QuotaKey {
    fn from_scope(scope: QuotaScope, req: &QuotaRequest<'_>) -> Option<Self> {
        let parts = match scope {
            QuotaScope::Global => vec!["*".to_string()],
            QuotaScope::User => req.user_id.map(|v| vec![v.to_string()])?,
            QuotaScope::Project => req.project_id.map(|v| vec![v.to_string()])?,
            QuotaScope::Provider => req.provider.map(|v| vec![v.to_string()])?,
            QuotaScope::Tool => req.tool.map(|v| vec![v.to_string()])?,
            QuotaScope::UserProject => match (req.user_id, req.project_id) {
                (Some(u), Some(p)) => Some(vec![u.to_string(), p.to_string()]),
                _ => None,
            }?,
            QuotaScope::UserProvider => match (req.user_id, req.provider) {
                (Some(u), Some(p)) => Some(vec![u.to_string(), p.to_string()]),
                _ => None,
            }?,
            QuotaScope::UserTool => match (req.user_id, req.tool) {
                (Some(u), Some(t)) => Some(vec![u.to_string(), t.to_string()]),
                _ => None,
            }?,
            QuotaScope::ProjectProvider => match (req.project_id, req.provider) {
                (Some(p), Some(v)) => Some(vec![p.to_string(), v.to_string()]),
                _ => None,
            }?,
            QuotaScope::ProjectTool => match (req.project_id, req.tool) {
                (Some(p), Some(t)) => Some(vec![p.to_string(), t.to_string()]),
                _ => None,
            }?,
        };
        Some(Self(format!("{}:{}", scope.as_str(), parts.join(":"))))
    }
}

/// Enforces per-scope rate, concurrency, and token budgets.
///
/// The manager keeps an in-memory cache of sliding-window counters. It is
/// intentionally self-contained so that it can be snapshotted for probes and
/// swapped for a persistent store without changing domain semantics.
#[derive(Debug)]
pub struct QuotaManager {
    limits: HashMap<QuotaScope, QuotaLimit>,
    counters: Mutex<HashMap<QuotaKey, Arc<WindowCounters>>>,
    /// Serialises the check-and-reserve operation.  Atomic counters alone are
    /// insufficient: a concurrent pair of callers can both observe capacity
    /// and then both admit, oversubscribing the configured limit.
    admission: Mutex<()>,
}

impl QuotaManager {
    #[must_use]
    pub fn new(limits: HashMap<QuotaScope, QuotaLimit>) -> Self {
        let mut limits = limits;
        limits
            .entry(QuotaScope::Global)
            .or_insert(QuotaLimit::default_global());
        Self {
            limits,
            counters: Mutex::new(HashMap::new()),
            admission: Mutex::new(()),
        }
    }

    /// Returns a reasonable default quota map for service mode.
    #[must_use]
    pub fn service_defaults() -> Self {
        let mut limits = HashMap::new();
        limits.insert(QuotaScope::Global, QuotaLimit::default_global());
        limits.insert(
            QuotaScope::User,
            QuotaLimit {
                requests_per_minute: Some(30),
                requests_per_hour: Some(200),
                requests_per_day: Some(1_000),
                max_concurrent: Some(2),
                input_tokens_per_hour: Some(1_000_000),
                output_tokens_per_hour: Some(500_000),
            },
        );
        limits.insert(
            QuotaScope::Project,
            QuotaLimit {
                requests_per_minute: Some(20),
                requests_per_hour: Some(150),
                requests_per_day: Some(800),
                max_concurrent: Some(2),
                input_tokens_per_hour: Some(500_000),
                output_tokens_per_hour: Some(250_000),
            },
        );
        limits.insert(
            QuotaScope::Provider,
            QuotaLimit {
                requests_per_minute: Some(40),
                requests_per_hour: Some(300),
                requests_per_day: Some(2_000),
                max_concurrent: Some(3),
                input_tokens_per_hour: Some(2_000_000),
                output_tokens_per_hour: Some(1_000_000),
            },
        );
        Self::new(limits)
    }

    /// Checks every configured scope and returns the most restrictive verdict.
    pub fn check(&self, req: &QuotaRequest<'_>) -> QuotaVerdict {
        let _guard = self
            .admission
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        self.check_unlocked(req)
    }

    fn check_unlocked(&self, req: &QuotaRequest<'_>) -> QuotaVerdict {
        let mut final_verdict: Option<QuotaVerdict> = None;

        for (scope, limit) in &self.limits {
            let Some(key) = QuotaKey::from_scope(*scope, req) else {
                continue;
            };
            let counters = self
                .counters
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .entry(key)
                .or_default()
                .clone();
            let verdict = Self::evaluate_scope(*scope, limit, &counters, req);

            if !verdict.allowed {
                return verdict;
            }
            final_verdict = Some(match final_verdict {
                Some(prev) if verdict.remaining < prev.remaining => verdict,
                None => verdict,
                Some(prev) => prev,
            });
        }

        final_verdict.unwrap_or_else(|| {
            QuotaVerdict::allowed(u64::MAX, u64::MAX, "none", "no quota configured")
        })
    }

    /// Checks and atomically reserves quota for a request.
    ///
    /// Callers should use this method instead of calling `check` followed by
    /// `admit`; the latter sequence is inherently racy under concurrency.
    pub fn try_admit(&self, req: &QuotaRequest<'_>) -> QuotaVerdict {
        let _guard = self
            .admission
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let verdict = self.check_unlocked(req);
        if !verdict.allowed {
            return verdict;
        }

        let now = now_seconds();
        for scope in self.limits.keys() {
            let Some(key) = QuotaKey::from_scope(*scope, req) else {
                continue;
            };
            let counters = self
                .counters
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .entry(key)
                .or_default()
                .clone();
            Self::tick_windows(&counters, now);
            counters.minute_count.fetch_add(1, Ordering::Relaxed);
            counters.hour_count.fetch_add(1, Ordering::Relaxed);
            counters.day_count.fetch_add(1, Ordering::Relaxed);
            counters
                .input_tokens_hour
                .fetch_add(req.input_tokens, Ordering::Relaxed);
            counters
                .output_tokens_hour
                .fetch_add(req.output_tokens, Ordering::Relaxed);
            counters.concurrent.fetch_add(1, Ordering::Relaxed);
        }
        verdict
    }

    /// Compatibility wrapper for callers that have already checked a request.
    /// New code should use [`Self::try_admit`] so reservation is atomic with
    /// validation.
    pub fn admit(&self, req: &QuotaRequest<'_>) {
        let _ = self.try_admit(req);
    }

    /// Decrements the concurrency counter. Call when the request completes or
    /// is cancelled.
    pub fn release(&self, req: &QuotaRequest<'_>) {
        let _guard = self
            .admission
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        for scope in self.limits.keys() {
            let Some(key) = QuotaKey::from_scope(*scope, req) else {
                continue;
            };
            if let Some(counters) = self
                .counters
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .get(&key)
                .cloned()
            {
                let _ =
                    counters
                        .concurrent
                        .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |v| {
                            Some(v.saturating_sub(1))
                        });
            }
        }
    }

    fn evaluate_scope(
        scope: QuotaScope,
        limit: &QuotaLimit,
        counters: &WindowCounters,
        req: &QuotaRequest<'_>,
    ) -> QuotaVerdict {
        let now = now_seconds();
        Self::tick_windows(counters, now);

        let minute = counters.minute_count.load(Ordering::Relaxed);
        let hour = counters.hour_count.load(Ordering::Relaxed);
        let day = counters.day_count.load(Ordering::Relaxed);
        let concurrent = counters.concurrent.load(Ordering::Relaxed);
        let in_tok = counters.input_tokens_hour.load(Ordering::Relaxed);
        let out_tok = counters.output_tokens_hour.load(Ordering::Relaxed);

        if let Some(cap) = limit.max_concurrent {
            if concurrent >= cap {
                return QuotaVerdict::denied(
                    cap,
                    cap.saturating_sub(concurrent),
                    scope.as_str(),
                    format!("concurrent limit {cap} reached ({concurrent} active)"),
                );
            }
        }

        if let Some(cap) = limit.requests_per_minute {
            let remaining = cap.saturating_sub(minute);
            if remaining == 0 {
                return QuotaVerdict::denied(
                    cap,
                    0,
                    scope.as_str(),
                    "per-minute rate limit exhausted",
                );
            }
        }

        if let Some(cap) = limit.requests_per_hour {
            let remaining = cap.saturating_sub(hour);
            if remaining == 0 {
                return QuotaVerdict::denied(
                    cap,
                    0,
                    scope.as_str(),
                    "per-hour rate limit exhausted",
                );
            }
        }

        if let Some(cap) = limit.requests_per_day {
            let remaining = cap.saturating_sub(day);
            if remaining == 0 {
                return QuotaVerdict::denied(
                    cap,
                    0,
                    scope.as_str(),
                    "per-day rate limit exhausted",
                );
            }
        }

        if let Some(cap) = limit.input_tokens_per_hour {
            let projected = in_tok + req.input_tokens;
            if projected > cap {
                return QuotaVerdict::denied(
                    cap,
                    cap.saturating_sub(in_tok),
                    scope.as_str(),
                    "input-token hourly budget exhausted",
                );
            }
        }

        if let Some(cap) = limit.output_tokens_per_hour {
            let projected = out_tok + req.output_tokens;
            if projected > cap {
                return QuotaVerdict::denied(
                    cap,
                    cap.saturating_sub(out_tok),
                    scope.as_str(),
                    "output-token hourly budget exhausted",
                );
            }
        }

        // Report the tightest remaining budget for observability.
        let (limit, remaining) = Self::tightest_remaining(limit, minute, hour, day);
        QuotaVerdict::allowed(limit, remaining, scope.as_str(), "within quota")
    }

    fn tick_windows(counters: &WindowCounters, now: u64) {
        let minute_bucket = now / 60;
        let hour_bucket = now / 3_600;
        let day_bucket = now / 86_400;

        let old_minute = counters.minute_start.load(Ordering::Relaxed);
        if old_minute != minute_bucket {
            counters
                .minute_start
                .store(minute_bucket, Ordering::Relaxed);
            counters.minute_count.store(0, Ordering::Relaxed);
        }

        let old_hour = counters.hour_start.load(Ordering::Relaxed);
        if old_hour != hour_bucket {
            counters.hour_start.store(hour_bucket, Ordering::Relaxed);
            counters.hour_count.store(0, Ordering::Relaxed);
            counters.input_tokens_hour.store(0, Ordering::Relaxed);
            counters.output_tokens_hour.store(0, Ordering::Relaxed);
        }

        let old_day = counters.day_start.load(Ordering::Relaxed);
        if old_day != day_bucket {
            counters.day_start.store(day_bucket, Ordering::Relaxed);
            counters.day_count.store(0, Ordering::Relaxed);
        }
    }

    fn tightest_remaining(limit: &QuotaLimit, minute: u64, hour: u64, day: u64) -> (u64, u64) {
        let mut limit_val = u64::MAX;
        let mut remaining = u64::MAX;
        for (cap, used) in [
            (limit.requests_per_minute, minute),
            (limit.requests_per_hour, hour),
            (limit.requests_per_day, day),
        ] {
            if let Some(cap) = cap {
                let rem = cap.saturating_sub(used);
                if rem < remaining {
                    remaining = rem;
                    limit_val = cap;
                }
            }
        }
        if limit_val == u64::MAX {
            (u64::MAX, u64::MAX)
        } else {
            (limit_val, remaining)
        }
    }
}

fn now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn global_rate_limit_allows_then_denies() {
        let mgr = QuotaManager::new(HashMap::from([(
            QuotaScope::Global,
            QuotaLimit {
                requests_per_minute: Some(2),
                ..QuotaLimit::unlimited()
            },
        )]));

        let req = QuotaRequest::default();
        assert!(mgr.check(&req).allowed);
        mgr.admit(&req);
        assert!(mgr.check(&req).allowed);
        mgr.admit(&req);
        let v = mgr.check(&req);
        assert!(!v.allowed, "{v:?}");
        assert_eq!(v.scope, "global");
    }

    #[test]
    fn concurrency_limit_blocks_excess() {
        let mgr = QuotaManager::new(HashMap::from([(
            QuotaScope::Global,
            QuotaLimit {
                max_concurrent: Some(1),
                ..QuotaLimit::unlimited()
            },
        )]));
        let req = QuotaRequest::default();
        mgr.admit(&req);
        assert!(!mgr.check(&req).allowed);
        mgr.release(&req);
        assert!(mgr.check(&req).allowed);
    }

    #[test]
    fn token_budget_enforced() {
        let mgr = QuotaManager::new(HashMap::from([(
            QuotaScope::User,
            QuotaLimit {
                input_tokens_per_hour: Some(100),
                output_tokens_per_hour: Some(50),
                ..QuotaLimit::unlimited()
            },
        )]));
        let req = QuotaRequest {
            user_id: Some("alice"),
            input_tokens: 80,
            output_tokens: 30,
            ..QuotaRequest::default()
        };
        assert!(mgr.check(&req).allowed);
        mgr.admit(&req);
        let next = QuotaRequest {
            user_id: Some("alice"),
            input_tokens: 30,
            output_tokens: 0,
            ..QuotaRequest::default()
        };
        assert!(!mgr.check(&next).allowed);
    }
}
