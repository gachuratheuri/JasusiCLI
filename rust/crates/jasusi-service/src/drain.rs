use std::sync::Arc;
use std::time::{Duration, Instant};

use tokio::sync::{watch, RwLock};

/// Manages graceful draining for the service.
///
/// When draining is enabled, new work is refused, existing in-flight work is
/// allowed to finish until the timeout, and then shutdown completes.
#[derive(Debug, Clone)]
pub struct DrainManager {
    state: Arc<RwLock<DrainState>>,
    drain_tx: watch::Sender<bool>,
    drain_rx: watch::Receiver<bool>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DrainState {
    Active,
    Draining { started: Instant, timeout: Duration },
    Complete,
}

impl DrainManager {
    #[must_use]
    pub fn new() -> Self {
        let (drain_tx, drain_rx) = watch::channel(false);
        Self {
            state: Arc::new(RwLock::new(DrainState::Active)),
            drain_tx,
            drain_rx,
        }
    }

    /// Starts draining with the given timeout. Returns false if already
    /// complete.
    pub async fn start(&self, timeout: Duration) -> bool {
        let mut state = self.state.write().await;
        if *state == DrainState::Complete {
            false
        } else {
            *state = DrainState::Draining {
                started: Instant::now(),
                timeout,
            };
            let _ = self.drain_tx.send(true);
            true
        }
    }

    /// Marks draining as complete, allowing shutdown to proceed.
    pub async fn complete(&self) {
        let mut state = self.state.write().await;
        *state = DrainState::Complete;
        let _ = self.drain_tx.send(true);
    }

    /// Returns true if draining is in progress or complete.
    pub async fn is_draining(&self) -> bool {
        matches!(
            *self.state.read().await,
            DrainState::Draining { .. } | DrainState::Complete
        )
    }

    /// Returns true once draining has exceeded its timeout or been marked
    /// complete.
    pub async fn should_shutdown(&self) -> bool {
        let state = *self.state.read().await;
        match state {
            DrainState::Active => false,
            DrainState::Draining { started, timeout } => started.elapsed() >= timeout,
            DrainState::Complete => true,
        }
    }

    /// Returns a receiver that becomes `true` when draining starts.
    #[must_use]
    pub fn shutdown_signal(&self) -> watch::Receiver<bool> {
        self.drain_rx.clone()
    }

    /// Waits for the shutdown signal or the supplied timeout, whichever is
    /// shorter.
    pub async fn wait_for_shutdown(&self, timeout: Duration) {
        let mut rx = self.shutdown_signal();
        // `watch::Receiver::changed` only resolves after a new value is
        // published.  A caller that subscribes after draining has already
        // started would otherwise wait for the timeout even though shutdown
        // is already in progress.
        if *rx.borrow() {
            return;
        }
        let _ = tokio::time::timeout(timeout, rx.changed()).await;
    }
}

impl Default for DrainManager {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn start_and_complete_drain() {
        let dm = DrainManager::new();
        assert!(!dm.is_draining().await);
        assert!(dm.start(Duration::from_secs(60)).await);
        assert!(dm.is_draining().await);
        assert!(!dm.should_shutdown().await);
        dm.complete().await;
        assert!(dm.should_shutdown().await);
    }

    #[tokio::test]
    async fn shutdown_signal_fires() {
        let dm = DrainManager::new();
        let mut rx = dm.shutdown_signal();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(10)).await;
            dm.start(Duration::from_secs(1)).await;
        });
        let fired = rx.changed().await.is_ok();
        assert!(fired);
    }
}
