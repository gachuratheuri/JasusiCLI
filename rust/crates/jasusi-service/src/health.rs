use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::{Duration, Instant};

/// A readiness or liveness probe for a single component.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Probe {
    /// Ready means the component can accept work.
    Ready { reason: String },
    /// `NotReady` means the component is alive but not able to accept work.
    NotReady { reason: String },
    /// Unknown means the component has not reported recently.
    Unknown,
}

impl Probe {
    #[must_use]
    pub fn is_ready(&self) -> bool {
        matches!(self, Self::Ready { .. })
    }
}

/// Snapshot of one component's health.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ComponentHealth {
    pub name: String,
    pub healthy: bool,
    pub ready: bool,
    pub reason: String,
    pub last_update: Instant,
}

/// Registry of component readiness probes used for `/ready` and gRPC
/// readiness checks.
///
/// All readiness decisions are derived from registered probes. There is no
/// static `ready: true` stub.
#[derive(Debug)]
pub struct HealthRegistry {
    components: Arc<RwLock<HashMap<String, (ComponentHealth, Probe)>>>,
    /// Maximum age before a component is considered stale/unknown.
    staleness: Duration,
}

impl HealthRegistry {
    #[must_use]
    pub fn new() -> Self {
        Self::with_staleness(Duration::from_secs(60))
    }

    #[must_use]
    pub fn with_staleness(staleness: Duration) -> Self {
        Self {
            components: Arc::new(RwLock::new(HashMap::new())),
            staleness,
        }
    }

    /// Registers or updates a probe for a named component.
    pub fn register(&self, name: impl Into<String>, probe: Probe) {
        let name = name.into();
        let now = Instant::now();
        let (healthy, ready, reason) = match &probe {
            Probe::Ready { reason } => (true, true, reason.clone()),
            Probe::NotReady { reason } => (true, false, reason.clone()),
            Probe::Unknown => (false, false, "no recent probe".to_string()),
        };
        let health = ComponentHealth {
            name: name.clone(),
            healthy,
            ready,
            reason,
            last_update: now,
        };
        self.components
            .write()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .insert(name, (health, probe));
    }

    /// Marks a component as unhealthy.
    pub fn mark_unhealthy(&self, name: impl AsRef<str>, reason: impl Into<String>) {
        self.register(
            name.as_ref().to_string(),
            Probe::NotReady {
                reason: reason.into(),
            },
        );
    }

    /// Returns true only if every registered component is ready and not stale.
    #[must_use]
    pub fn is_ready(&self) -> bool {
        let components = self
            .components
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        !components.is_empty()
            && components.values().all(|(health, _probe)| {
                health.ready && health.last_update.elapsed() <= self.staleness
            })
    }

    /// Returns a snapshot of all components, including stale ones.
    #[must_use]
    pub fn snapshot(&self) -> Vec<ComponentHealth> {
        let components = self
            .components
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut out = Vec::with_capacity(components.len());
        for (health, _probe) in components.values() {
            let mut health = health.clone();
            if health.last_update.elapsed() > self.staleness {
                health.ready = false;
                health.reason = "probe stale".to_string();
            }
            out.push(health);
        }
        out
    }

    /// Returns the overall readiness verdict and component list.
    #[must_use]
    pub fn readiness_status(&self) -> (bool, Vec<ComponentHealth>) {
        let snapshot = self.snapshot();
        let ready = !snapshot.is_empty() && snapshot.iter().all(|c| c.ready);
        (ready, snapshot)
    }
}

impl Default for HealthRegistry {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn empty_registry_is_not_ready() {
        let h = HealthRegistry::new();
        assert!(!h.is_ready());
    }

    #[test]
    fn readiness_requires_all_components_ready() {
        let h = HealthRegistry::new();
        h.register(
            "store",
            Probe::Ready {
                reason: "ok".into(),
            },
        );
        h.register(
            "provider",
            Probe::NotReady {
                reason: "auth missing".into(),
            },
        );
        assert!(!h.is_ready());
        h.register(
            "provider",
            Probe::Ready {
                reason: "auth ok".into(),
            },
        );
        assert!(h.is_ready());
    }

    #[test]
    fn stale_probe_blocks_readiness() {
        let h = HealthRegistry::with_staleness(Duration::from_nanos(1));
        h.register(
            "store",
            Probe::Ready {
                reason: "ok".into(),
            },
        );
        std::thread::sleep(Duration::from_millis(5));
        assert!(!h.is_ready());
    }
}
