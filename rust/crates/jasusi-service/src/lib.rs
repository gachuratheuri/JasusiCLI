//! Service-mode control plane for Jasusi.
//!
//! This crate owns phase-8 reliability, observability, and controlled
//! scalability primitives: deployment profiles, durable job queues, per-scope
//! quotas, health/readiness probes, a self-contained metrics registry,
//! structured redacted audit logging, OpenTelemetry tracing, and graceful
//! draining. It deliberately avoids direct gRPC/Tonic dependencies so that
//! `jasusi-core` can expose its generated types while `jasusi-service` supplies
//! the engine-agnostic policy and state machinery.

pub mod audit;
pub mod drain;
pub mod health;
pub mod id;
pub mod metrics;
pub mod profile;
pub mod queue;
pub mod quota;

#[cfg(feature = "otel")]
pub mod otel;

pub use audit::{AuditEvent, AuditLog, Redactor};
pub use drain::DrainManager;
pub use health::{ComponentHealth, HealthRegistry, Probe};
pub use id::{CorrelationId, IdempotencyKey, JobId};
pub use metrics::{MetricFamily, MetricPoint, MetricsRecorder};
pub use profile::{DeploymentProfile, ServiceConfig};
pub use queue::{Job, JobEvent, JobQueue, JobState, QueueError};
pub use quota::{QuotaLimit, QuotaManager, QuotaRequest, QuotaScope, QuotaVerdict};

/// Result type used across service primitives.
pub type Result<T> = std::result::Result<T, ServiceError>;

/// Top-level error type for service-layer failures.
#[derive(Debug, thiserror::Error)]
pub enum ServiceError {
    #[error("quota exceeded: {0}")]
    QuotaExceeded(String),
    #[error("queue error: {0}")]
    Queue(#[from] QueueError),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("serialization error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("audit error: {0}")]
    Audit(String),
    #[error("{0}")]
    Other(String),
}

/// Shared context passed to every operation in service mode.
#[derive(Debug, Clone)]
pub struct ServiceContext {
    pub profile: DeploymentProfile,
    pub service_name: &'static str,
    pub version: String,
    pub git_sha: Option<String>,
    pub correlation_id: CorrelationId,
}

impl ServiceContext {
    #[must_use]
    pub fn new(profile: DeploymentProfile, version: impl Into<String>) -> Self {
        Self {
            profile,
            service_name: "jasusi-core",
            version: version.into(),
            git_sha: option_env!("GIT_SHA").map(std::string::String::from),
            correlation_id: CorrelationId::generate(),
        }
    }

    #[must_use]
    pub fn with_correlation(mut self, correlation_id: CorrelationId) -> Self {
        self.correlation_id = correlation_id;
        self
    }
}
