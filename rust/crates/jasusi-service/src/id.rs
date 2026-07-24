use std::fmt;
use std::hash::Hash;
use std::sync::atomic::{AtomicU64, Ordering};

use regex::Regex;
use sha2::{Digest, Sha256};
use uuid::Uuid;

static ID_COUNTER: AtomicU64 = AtomicU64::new(1);

/// A stable correlation identifier propagated across RPC, HTTP, provider, tool,
/// and persistence boundaries. It is 7-bit ASCII and URL-safe.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(transparent)]
pub struct CorrelationId(String);

impl CorrelationId {
    const MAX_LEN: usize = 64;

    #[must_use]
    pub fn generate() -> Self {
        Self(format!(
            "{}-{:016x}",
            Uuid::new_v4().to_string().replace('-', ""),
            ID_COUNTER.fetch_add(1, Ordering::Relaxed)
        ))
    }

    /// Parses and validates a caller-supplied correlation id.
    ///
    /// # Errors
    ///
    /// Returns an error if the value is empty, too long, or contains characters
    /// outside the permitted `[A-Za-z0-9_.-]` set.
    pub fn parse(value: impl AsRef<str>) -> Result<Self, String> {
        let value = value.as_ref();
        if value.is_empty() || value.len() > Self::MAX_LEN {
            return Err(format!(
                "correlation id length must be 1..={}",
                Self::MAX_LEN
            ));
        }
        let re = Regex::new(r"^[A-Za-z0-9_.-]+$").expect("valid regex");
        if !re.is_match(value) {
            return Err("correlation id contains illegal characters".to_string());
        }
        Ok(Self(value.to_string()))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl Default for CorrelationId {
    fn default() -> Self {
        Self::generate()
    }
}

impl fmt::Display for CorrelationId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// A validated idempotency key used for mutation and job-submission endpoints.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(transparent)]
pub struct IdempotencyKey(String);

impl IdempotencyKey {
    const MAX_LEN: usize = 128;

    #[must_use]
    pub fn new() -> Self {
        Self(CorrelationId::generate().to_string())
    }

    /// Derives a tenant/project-scoped key while retaining deterministic
    /// retries for the same logical request.  This prevents a caller from one
    /// project replaying or observing a key belonging to another project.
    #[must_use]
    pub fn scoped(scope: &[&str], key: &Self) -> Self {
        let mut hasher = Sha256::new();
        for part in scope {
            hasher.update(part.as_bytes());
            hasher.update([0]);
        }
        hasher.update(key.as_str().as_bytes());
        Self(hex::encode(hasher.finalize()))
    }

    /// Parses and validates a caller-supplied idempotency key.
    ///
    /// # Errors
    ///
    /// Returns an error if the value is empty, too long, or contains characters
    /// outside the permitted `[A-Za-z0-9_.=-]` set.
    pub fn parse(value: impl AsRef<str>) -> Result<Self, String> {
        let value = value.as_ref();
        if value.is_empty() || value.len() > Self::MAX_LEN {
            return Err(format!(
                "idempotency key length must be 1..={}",
                Self::MAX_LEN
            ));
        }
        let re = Regex::new(r"^[A-Za-z0-9_.=-]+$").expect("valid regex");
        if !re.is_match(value) {
            return Err("idempotency key contains illegal characters".to_string());
        }
        Ok(Self(value.to_string()))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl Default for IdempotencyKey {
    fn default() -> Self {
        Self::new()
    }
}

impl fmt::Display for IdempotencyKey {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

/// A short, URL-safe identifier for a queued job.
#[derive(Debug, Clone, PartialEq, Eq, Hash, serde::Serialize, serde::Deserialize)]
#[serde(transparent)]
pub struct JobId(String);

impl JobId {
    #[must_use]
    pub fn generate() -> Self {
        Self(Uuid::new_v4().to_string())
    }

    pub fn parse(value: impl AsRef<str>) -> Result<Self, String> {
        let value = value.as_ref();
        if value.is_empty() || value.len() > 64 {
            return Err("job id length must be 1..=64".to_string());
        }
        let re = Regex::new(r"^[A-Za-z0-9_.=-]+$").expect("valid regex");
        if !re.is_match(value) {
            return Err("job id contains illegal characters".to_string());
        }
        Ok(Self(value.to_string()))
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for JobId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn correlation_id_is_url_safe_and_unique() {
        let a = CorrelationId::generate();
        let b = CorrelationId::generate();
        assert_ne!(a, b);
        assert!(!a.as_str().is_empty());
        assert!(a.as_str().len() <= CorrelationId::MAX_LEN);
        assert!(CorrelationId::parse(a.as_str()).is_ok());
    }

    #[test]
    fn correlation_id_rejects_dangerous_characters() {
        assert!(CorrelationId::parse("foo/bar").is_err());
        assert!(CorrelationId::parse("foo bar").is_err());
        assert!(CorrelationId::parse("foo\nbar").is_err());
    }

    #[test]
    fn idempotency_key_rejects_newline() {
        assert!(IdempotencyKey::parse("ok-key_123").is_ok());
        assert!(IdempotencyKey::parse("bad\nkey").is_err());
    }

    #[test]
    fn scoped_idempotency_keys_are_deterministic_but_isolated() {
        let key = IdempotencyKey::parse("request-1").unwrap();
        let first = IdempotencyKey::scoped(&["alice", "project-a"], &key);
        let retry = IdempotencyKey::scoped(&["alice", "project-a"], &key);
        let other = IdempotencyKey::scoped(&["alice", "project-b"], &key);
        assert_eq!(first, retry);
        assert_ne!(first, other);
        assert!(IdempotencyKey::parse(first.as_str()).is_ok());
    }

    #[test]
    fn job_id_parses_uuid_form() {
        let id = JobId::generate();
        assert!(JobId::parse(id.as_str()).is_ok());
    }
}
