// NOT-YET-WIRED SUBSYSTEM.
//
// The items below are implemented but not reachable from any entry point: the
// gRPC service that will consume them still returns `UNIMPLEMENTED` (F02), and
// the Python adapter has not been migrated onto it (F01). The allowance is
// scoped to this module and names the reason, so the unreachability stays
// visible and auditable. Do NOT widen it to the crate, and do NOT add
// `unused_imports`/`unused_variables` here — a blanket crate-level allow is
// exactly what previously concealed three unreachable security controls.
#![allow(dead_code)]

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    pub seq: u64,
    pub timestamp: String,
    pub session_id: String,
    pub event_type: AuditEventType,
    pub actor: String,
    pub outcome: AuditOutcome,
    pub input_hash: String,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum AuditEventType {
    ToolCall,
    SecurityViolation,
    CompactionTriggered,
    MemoryUpsert,
    SessionCreated,
    SessionExpired,
    PermissionGranted,
    PermissionDenied,
    RollbackExecuted,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum AuditOutcome {
    Success,
    Failure,
    Blocked,
    Quarantined,
}

impl AuditEntry {
    pub fn new(
        seq: u64,
        session_id: impl Into<String>,
        event_type: AuditEventType,
        actor: impl Into<String>,
        outcome: AuditOutcome,
        input_hash: impl Into<String>,
        detail: impl Into<String>,
    ) -> Self {
        Self {
            seq,
            timestamp: chrono::Utc::now().to_rfc3339(),
            session_id: session_id.into(),
            event_type,
            actor: actor.into(),
            outcome,
            input_hash: input_hash.into(),
            detail: detail.into(),
        }
    }
}
