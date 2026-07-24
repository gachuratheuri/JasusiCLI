use std::fs;

use jasusi_core::audit::entry::{AuditEventType, AuditOutcome};
use jasusi_core::audit::ledger::{TamperStatus, WormLedger};
use jasusi_core::memory::legacy_migration::migrate_legacy_json_sessions;
use jasusi_core::memory::session_store::SessionStore;
use tempfile::tempdir;

#[test]
fn test_ledger_sha256_chain_verification_and_tamper_detection() {
    let dir = tempdir().unwrap();
    let db_path = dir.path().join("audit.db");

    let ledger = WormLedger::open(db_path.clone()).unwrap();

    // Append 3 audit records
    ledger
        .append_blocking(
            "sess-1",
            &AuditEventType::SessionCreated,
            "system",
            &AuditOutcome::Success,
            "hash1",
            "session started",
        )
        .unwrap();

    ledger
        .append_blocking(
            "sess-1",
            &AuditEventType::ToolCall,
            "agent",
            &AuditOutcome::Success,
            "hash2",
            "executed tool",
        )
        .unwrap();

    ledger
        .append_blocking(
            "sess-1",
            &AuditEventType::RollbackExecuted,
            "security",
            &AuditOutcome::Success,
            "hash3",
            "rollback applied",
        )
        .unwrap();

    // Verification on clean ledger must report Clean
    let status = ledger.check_tamper_status().unwrap();
    assert_eq!(status, TamperStatus::Clean);

    // Tamper with record 2 directly in SQLite database
    {
        let conn = rusqlite::Connection::open(&db_path).unwrap();
        conn.execute(
            "UPDATE audit_log SET detail = 'tampered detail' WHERE seq = 2",
            [],
        )
        .unwrap();
    }

    // Verification on tampered ledger must detect ChainBroken / Tampered
    let status_tampered = ledger.check_tamper_status().unwrap();
    assert_eq!(
        status_tampered,
        TamperStatus::Tampered {
            corrupted_sequence: 2
        }
    );
}

#[test]
fn test_legacy_session_json_migration() {
    let legacy_dir = tempdir().unwrap();
    let store_dir = tempdir().unwrap();

    // Create 2 legacy JSON files
    let legacy1 = r#"{
        "session_id": "legacy-sess-1",
        "project": "my-project",
        "input_tokens": 150,
        "output_tokens": 50
    }"#;

    let legacy2 = r#"{
        "session_id": "legacy-sess-2",
        "project": "my-project"
    }"#;

    fs::write(legacy_dir.path().join("sess1.json"), legacy1).unwrap();
    fs::write(legacy_dir.path().join("sess2.json"), legacy2).unwrap();

    let store = SessionStore::open(store_dir.path().to_path_buf()).unwrap();
    let report = migrate_legacy_json_sessions(legacy_dir.path(), &store).unwrap();

    assert_eq!(report.total_found, 2);
    assert_eq!(report.total_migrated, 2);
    assert_eq!(report.total_skipped, 0);

    // Verify session store contains migrated sessions
    let meta1 = store.get_session("legacy-sess-1").unwrap();
    assert_eq!(meta1.project, "my-project");

    let meta2 = store.get_session("legacy-sess-2").unwrap();
    assert_eq!(meta2.project, "my-project");
}
