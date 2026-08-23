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

use std::fs;
use std::path::Path;

use serde::{Deserialize, Serialize};

use crate::memory::session_store::{SessionStore, StoreError};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LegacySessionJson {
    pub session_id: String,
    pub project: Option<String>,
    pub created_at: Option<String>,
    pub updated_at: Option<String>,
    pub input_tokens: Option<u64>,
    pub output_tokens: Option<u64>,
    pub messages: Option<Vec<serde_json::Value>>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MigrationReport {
    pub total_found: usize,
    pub total_migrated: usize,
    pub total_skipped: usize,
    pub validation_messages: Vec<String>,
}

/// Migrate legacy JSON session files into canonical SessionStore storage.
pub fn migrate_legacy_json_sessions(
    legacy_dir: &Path,
    target_store: &SessionStore,
) -> Result<MigrationReport, StoreError> {
    let mut report = MigrationReport {
        total_found: 0,
        total_migrated: 0,
        total_skipped: 0,
        validation_messages: Vec::new(),
    };

    if !legacy_dir.exists() {
        report
            .validation_messages
            .push("Legacy directory does not exist".to_string());
        return Ok(report);
    }

    let entries = fs::read_dir(legacy_dir).map_err(|e| StoreError(e.to_string()))?;
    for entry in entries {
        let entry = entry.map_err(|e| StoreError(e.to_string()))?;
        let path = entry.path();
        if path.is_file() && path.extension().is_some_and(|ext| ext == "json") {
            report.total_found += 1;
            if let Ok(content) = fs::read_to_string(&path) {
                if let Ok(legacy) = serde_json::from_str::<LegacySessionJson>(&content) {
                    let project = legacy.project.unwrap_or_else(|| "default".to_string());
                    if target_store
                        .create_session(&legacy.session_id, &project)
                        .is_ok()
                    {
                        report.total_migrated += 1;
                    } else {
                        report.total_skipped += 1;
                    }
                } else {
                    report.total_skipped += 1;
                }
            } else {
                report.total_skipped += 1;
            }
        }
    }

    report.validation_messages.push(format!(
        "Migrated {}/{} legacy session files",
        report.total_migrated, report.total_found
    ));

    Ok(report)
}
