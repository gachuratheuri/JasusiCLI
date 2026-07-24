use std::fmt;
use std::str::FromStr;

/// Deployment profile for a running Jasusi instance.
///
/// * `Local`  — single user, single engine, bounded workers, local-first store.
/// * `Service`— stateless web adapter, durable job queue, multi-tenant quotas.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum DeploymentProfile {
    Local,
    Service,
}

impl DeploymentProfile {
    pub const ENV_VAR: &'static str = "JASUSI_DEPLOYMENT_PROFILE";
    pub const DEFAULT: Self = Self::Local;

    #[must_use]
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Local => "local",
            Self::Service => "service",
        }
    }

    /// Resolves the active profile from the environment, defaulting to `Local`.
    #[must_use]
    pub fn from_env() -> Self {
        std::env::var(Self::ENV_VAR)
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(Self::DEFAULT)
    }

    #[must_use]
    pub fn is_service(&self) -> bool {
        matches!(self, Self::Service)
    }

    #[must_use]
    pub fn is_local(&self) -> bool {
        matches!(self, Self::Local)
    }
}

impl Default for DeploymentProfile {
    fn default() -> Self {
        Self::DEFAULT
    }
}

impl FromStr for DeploymentProfile {
    type Err = String;

    fn from_str(s: &str) -> std::result::Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "local" | "dev" | "development" => Ok(Self::Local),
            "service" | "prod" | "production" => Ok(Self::Service),
            other => Err(format!("unknown deployment profile: {other}")),
        }
    }
}

impl fmt::Display for DeploymentProfile {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

/// Service-mode configuration: bounds, queue paths, and exporter targets.
#[derive(Debug, Clone)]
pub struct ServiceConfig {
    pub profile: DeploymentProfile,
    pub max_concurrent_jobs: usize,
    pub max_queue_size: usize,
    pub max_prompt_bytes: usize,
    pub max_event_bytes: usize,
    pub queue_db_path: std::path::PathBuf,
    pub audit_log_path: std::path::PathBuf,
    pub metrics_snapshot_ttl_seconds: u64,
    pub otel_enabled: bool,
    pub otel_stdout: bool,
    pub otel_otlp_endpoint: Option<String>,
    pub graceful_drain_timeout_seconds: u64,
    pub bind_host: String,
    pub bind_port: u16,
    pub control_port: u16,
    pub auth_bearer_token: Option<String>,
}

impl ServiceConfig {
    pub const DEFAULT_MAX_CONCURRENT_JOBS: usize = 4;
    pub const DEFAULT_MAX_QUEUE_SIZE: usize = 16;
    pub const DEFAULT_MAX_PROMPT_BYTES: usize = 64 * 1024;
    pub const DEFAULT_MAX_EVENT_BYTES: usize = 256 * 1024;
    pub const DEFAULT_METRICS_TTL_SECONDS: u64 = 300;
    pub const DEFAULT_DRAIN_TIMEOUT_SECONDS: u64 = 30;
    pub const DEFAULT_BIND_HOST: &'static str = "127.0.0.1";
    pub const DEFAULT_BIND_PORT: u16 = 50051;
    pub const DEFAULT_CONTROL_PORT: u16 = 50052;

    #[must_use]
    pub fn local() -> Self {
        let home = dirs::home_dir().unwrap_or_else(|| std::path::PathBuf::from("."));
        let mut cfg = Self {
            profile: DeploymentProfile::Local,
            max_concurrent_jobs: Self::DEFAULT_MAX_CONCURRENT_JOBS,
            max_queue_size: Self::DEFAULT_MAX_QUEUE_SIZE,
            max_prompt_bytes: Self::DEFAULT_MAX_PROMPT_BYTES,
            max_event_bytes: Self::DEFAULT_MAX_EVENT_BYTES,
            queue_db_path: home.join(".jasusi").join("service-queue.db"),
            audit_log_path: home.join(".jasusi").join("audit-service.jsonl"),
            metrics_snapshot_ttl_seconds: Self::DEFAULT_METRICS_TTL_SECONDS,
            otel_enabled: false,
            otel_stdout: true,
            otel_otlp_endpoint: None,
            graceful_drain_timeout_seconds: Self::DEFAULT_DRAIN_TIMEOUT_SECONDS,
            bind_host: Self::DEFAULT_BIND_HOST.to_string(),
            bind_port: Self::DEFAULT_BIND_PORT,
            control_port: Self::DEFAULT_CONTROL_PORT,
            auth_bearer_token: None,
        };
        cfg.apply_environment_overrides();
        cfg
    }

    #[must_use]
    pub fn service() -> Self {
        let mut cfg = Self::local();
        cfg.profile = DeploymentProfile::Service;
        cfg.max_concurrent_jobs = std::env::var("JASUSI_MAX_CONCURRENT_JOBS")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(16);
        cfg.apply_environment_overrides();
        cfg.max_queue_size = cfg.max_queue_size.max(cfg.max_concurrent_jobs);
        cfg
    }

    fn apply_environment_overrides(&mut self) {
        if let Ok(value) = std::env::var("JASUSI_MAX_QUEUE_SIZE") {
            if let Ok(parsed) = value.parse() {
                self.max_queue_size = parsed;
            }
        }
        if let Ok(value) = std::env::var("JASUSI_MAX_PROMPT_BYTES") {
            if let Ok(parsed) = value.parse() {
                self.max_prompt_bytes = parsed;
            }
        }
        if let Ok(value) = std::env::var("JASUSI_MAX_EVENT_BYTES") {
            if let Ok(parsed) = value.parse() {
                self.max_event_bytes = parsed;
            }
        }
        if let Ok(value) = std::env::var("JASUSI_GRACEFUL_DRAIN_TIMEOUT_SECONDS") {
            if let Ok(parsed) = value.parse() {
                self.graceful_drain_timeout_seconds = parsed;
            }
        }
        if let Ok(value) = std::env::var("JASUSI_BIND_HOST") {
            if !value.trim().is_empty() {
                self.bind_host = value;
            }
        }
        if let Ok(value) = std::env::var("JASUSI_BIND_PORT") {
            if let Ok(parsed) = value.parse() {
                self.bind_port = parsed;
            }
        }
        if let Ok(value) = std::env::var("JASUSI_CONTROL_PORT") {
            if let Ok(parsed) = value.parse() {
                self.control_port = parsed;
            }
        }
        self.auth_bearer_token = std::env::var("JASUSI_AUTH_BEARER_TOKEN")
            .ok()
            .filter(|v| !v.trim().is_empty());
    }

    /// Builds the active configuration from the environment and the resolved
    /// deployment profile.
    #[must_use]
    pub fn from_env() -> Self {
        match DeploymentProfile::from_env() {
            DeploymentProfile::Local => Self::local(),
            DeploymentProfile::Service => Self::service(),
        }
    }

    /// Enforces fail-closed semantics: service mode without an authentication
    /// token binding outside loopback is rejected.
    ///
    /// # Errors
    ///
    /// Returns an error if the configuration is insecure for service exposure.
    pub fn validate(&self) -> Result<(), String> {
        let host = self.bind_host.as_str();
        let is_loopback = host == "127.0.0.1" || host == "::1" || host == "localhost";

        if self.profile.is_service() && !is_loopback && self.auth_bearer_token.is_none() {
            return Err(
                "service mode on a non-loopback interface requires JASUSI_AUTH_BEARER_TOKEN"
                    .to_string(),
            );
        }
        if self.max_concurrent_jobs == 0 {
            return Err("max_concurrent_jobs must be greater than zero".to_string());
        }
        if self.max_queue_size < self.max_concurrent_jobs {
            return Err("max_queue_size must be at least max_concurrent_jobs".to_string());
        }
        if self.max_prompt_bytes == 0 || self.max_event_bytes == 0 {
            return Err("prompt and event bounds must be greater than zero".to_string());
        }
        Ok(())
    }
}

impl Default for ServiceConfig {
    fn default() -> Self {
        Self::from_env()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_parses_common_names() {
        assert_eq!(
            "local".parse::<DeploymentProfile>().unwrap(),
            DeploymentProfile::Local
        );
        assert_eq!(
            "service".parse::<DeploymentProfile>().unwrap(),
            DeploymentProfile::Service
        );
        assert!("unknown".parse::<DeploymentProfile>().is_err());
    }

    #[test]
    fn service_config_rejects_unauthenticated_public_bind() {
        let mut cfg = ServiceConfig::service();
        cfg.bind_host = "0.0.0.0".to_string();
        cfg.auth_bearer_token = None;
        assert!(cfg.validate().is_err());
        cfg.auth_bearer_token = Some("secret".to_string());
        assert!(cfg.validate().is_ok());
    }

    #[test]
    fn local_config_allows_loopback_without_token() {
        let cfg = ServiceConfig::local();
        assert!(cfg.validate().is_ok());
    }
}
