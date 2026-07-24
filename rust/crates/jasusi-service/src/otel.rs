//! OpenTelemetry-compatible tracing boundary.
//!
//! The service emits structured `tracing` spans with W3C trace-context fields
//! and deliberately keeps exporter selection outside the core crate.  This
//! avoids coupling the security/runtime binary to a particular collector while
//! allowing a deployment to attach an OpenTelemetry tracing subscriber at its
//! edge.  The guard provides lifecycle hooks and guarantees a final shutdown
//! event is emitted.

use std::time::Instant;

/// Lifecycle guard for service tracing.
#[derive(Debug)]
pub struct OtelGuard {
    service_name: String,
    service_version: String,
    started: Instant,
}

impl OtelGuard {
    /// Starts a tracing lifecycle for a service.
    pub fn new(
        service_name: impl Into<String>,
        service_version: impl Into<String>,
    ) -> Result<Self, String> {
        let guard = Self {
            service_name: service_name.into(),
            service_version: service_version.into(),
            started: Instant::now(),
        };
        tracing::info!(
            service = %guard.service_name,
            version = %guard.service_version,
            otel_semconv = "1.27",
            "telemetry_started"
        );
        Ok(guard)
    }

    /// Records a span boundary using the standard `tracing` subscriber.
    #[must_use]
    pub fn span(&self, operation: &str) -> tracing::Span {
        tracing::info_span!(
            "jasusi.operation",
            service.name = %self.service_name,
            service.version = %self.service_version,
            operation = %operation,
        )
    }

    /// Flush hook for exporter adapters.  Synchronous subscribers flush on
    /// their own schedule; this method provides a stable lifecycle boundary.
    pub fn flush(&self) {
        tracing::info!(
            service = %self.service_name,
            elapsed_ms = self.started.elapsed().as_millis() as u64,
            "telemetry_flush"
        );
    }
}

impl Drop for OtelGuard {
    fn drop(&mut self) {
        self.flush();
        tracing::info!(service = %self.service_name, "telemetry_shutdown");
    }
}

/// Validates and returns a W3C `traceparent` trace-id component.
pub fn trace_id_from_traceparent(value: &str) -> Option<String> {
    let mut fields = value.split('-');
    let version = fields.next()?;
    let trace_id = fields.next()?;
    let parent_id = fields.next()?;
    let flags = fields.next()?;
    if version != "00"
        || trace_id.len() != 32
        || parent_id.len() != 16
        || flags.len() != 2
        || trace_id.chars().all(|c| c == '0')
        || parent_id.chars().all(|c| c == '0')
    {
        return None;
    }
    if !trace_id.chars().all(|c| c.is_ascii_hexdigit())
        || !parent_id.chars().all(|c| c.is_ascii_hexdigit())
        || !flags.chars().all(|c| c.is_ascii_hexdigit())
    {
        return None;
    }
    Some(trace_id.to_ascii_lowercase())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tracing_guard_can_be_built() {
        let guard = OtelGuard::new("test-service", "0.0.0").unwrap();
        let _span = guard.span("test.operation");
        guard.flush();
    }

    #[test]
    fn traceparent_is_strictly_validated() {
        assert_eq!(
            trace_id_from_traceparent("00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"),
            Some("4bf92f3577b34da6a3ce929d0e0e4736".to_string())
        );
        assert!(trace_id_from_traceparent(
            "00-00000000000000000000000000000000-00f067aa0ba902b7-01"
        )
        .is_none());
        assert!(trace_id_from_traceparent("malformed").is_none());
    }
}
