use crate::audit::entry::{AuditEntry, AuditEventType, AuditOutcome};
use rusqlite::{params, Connection};
use sha2::{Digest, Sha256};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[derive(Debug)]
pub struct LedgerError(pub String);

impl std::fmt::Display for LedgerError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "LedgerError: {}", self.0)
    }
}

impl std::error::Error for LedgerError {}

pub struct WormLedger {
    conn: Arc<Mutex<Connection>>,
    db_path: PathBuf,
}

impl WormLedger {
    pub fn open(db_path: PathBuf) -> Result<Self, LedgerError> {
        if let Some(parent) = db_path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| LedgerError(e.to_string()))?;
        }
        let conn = Connection::open(&db_path).map_err(|e| LedgerError(e.to_string()))?;

        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=FULL;
             CREATE TABLE IF NOT EXISTS audit_log (
                 seq          INTEGER PRIMARY KEY AUTOINCREMENT,
                 timestamp    TEXT    NOT NULL,
                 session_id   TEXT    NOT NULL,
                 event_type   TEXT    NOT NULL,
                 actor        TEXT    NOT NULL,
                 outcome      TEXT    NOT NULL,
                 input_hash   TEXT    NOT NULL,
                 detail       TEXT    NOT NULL,
                 chain_hash   TEXT    NOT NULL
             );
             CREATE TABLE IF NOT EXISTS metadata (
                 key   TEXT PRIMARY KEY,
                 value TEXT NOT NULL
             );
             INSERT OR IGNORE INTO metadata(key, value) VALUES('version', '1');",
        )
        .map_err(|e| LedgerError(e.to_string()))?;

        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
            db_path,
        })
    }

    pub fn default_path() -> PathBuf {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".jasusi")
            .join("audit.db")
    }

    pub fn append_blocking(
        &self,
        session_id: &str,
        event_type: &AuditEventType,
        actor: &str,
        outcome: &AuditOutcome,
        input_hash: &str,
        detail: &str,
    ) -> Result<u64, LedgerError> {
        let conn = self.conn.lock().map_err(|e| LedgerError(e.to_string()))?;

        let prev_hash: String = conn
            .query_row(
                "SELECT chain_hash FROM audit_log ORDER BY seq DESC LIMIT 1",
                [],
                |row| row.get(0),
            )
            .unwrap_or_else(|_| "GENESIS".to_string());

        let chain_input = format!(
            "{}:{}:{}:{}:{}:{}",
            prev_hash,
            session_id,
            event_type_str(event_type),
            actor,
            input_hash,
            detail
        );
        let mut hasher = Sha256::new();
        hasher.update(chain_input.as_bytes());
        let chain_hash = hex::encode(hasher.finalize());

        let timestamp = chrono::Utc::now().to_rfc3339();
        conn.execute(
            "INSERT INTO audit_log
             (timestamp, session_id, event_type, actor, outcome, input_hash, detail, chain_hash)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
            params![
                timestamp,
                session_id,
                event_type_str(event_type),
                actor,
                outcome_str(outcome),
                input_hash,
                detail,
                chain_hash,
            ],
        )
        .map_err(|e| LedgerError(e.to_string()))?;

        let seq = u64::try_from(conn.last_insert_rowid())
            .map_err(|e| LedgerError(e.to_string()))?;
        Ok(seq)
    }

    pub fn verify_chain(&self) -> Result<Option<u64>, LedgerError> {
        let conn = self.conn.lock().map_err(|e| LedgerError(e.to_string()))?;
        let mut stmt = conn
            .prepare(
                "SELECT seq, session_id, event_type, actor, input_hash, detail, chain_hash
                 FROM audit_log ORDER BY seq ASC",
            )
            .map_err(|e| LedgerError(e.to_string()))?;

        let mut prev_hash = "GENESIS".to_string();
        let mut rows = stmt.query([]).map_err(|e| LedgerError(e.to_string()))?;

        while let Some(row) = rows.next().map_err(|e| LedgerError(e.to_string()))? {
            let seq: u64 = row.get(0).map_err(|e| LedgerError(e.to_string()))?;
            let session_id: String = row.get(1).map_err(|e| LedgerError(e.to_string()))?;
            let event_type: String = row.get(2).map_err(|e| LedgerError(e.to_string()))?;
            let actor: String = row.get(3).map_err(|e| LedgerError(e.to_string()))?;
            let input_hash: String = row.get(4).map_err(|e| LedgerError(e.to_string()))?;
            let detail: String = row.get(5).map_err(|e| LedgerError(e.to_string()))?;
            let stored_hash: String = row.get(6).map_err(|e| LedgerError(e.to_string()))?;

            let chain_input = format!(
                "{prev_hash}:{session_id}:{event_type}:{actor}:{input_hash}:{detail}"
            );
            let mut hasher = Sha256::new();
            hasher.update(chain_input.as_bytes());
            let expected_hash = hex::encode(hasher.finalize());

            if expected_hash != stored_hash {
                return Ok(Some(seq));
            }
            prev_hash = stored_hash;
        }
        Ok(None)
    }

    pub fn get_entries(
        &self,
        session_id: &str,
        limit: u32,
    ) -> Result<Vec<AuditEntry>, LedgerError> {
        let conn = self.conn.lock().map_err(|e| LedgerError(e.to_string()))?;
        let mut stmt = conn
            .prepare(
                "SELECT seq, timestamp, session_id, event_type, actor, outcome, input_hash, detail
                 FROM audit_log WHERE session_id = ?1 ORDER BY seq DESC LIMIT ?2",
            )
            .map_err(|e| LedgerError(e.to_string()))?;

        let entries = stmt
            .query_map(params![session_id, limit], |row| {
                Ok(AuditEntry {
                    seq: row.get(0)?,
                    timestamp: row.get(1)?,
                    session_id: row.get(2)?,
                    event_type: parse_event_type(row.get::<_, String>(3)?.as_str()),
                    actor: row.get(4)?,
                    outcome: parse_outcome(row.get::<_, String>(5)?.as_str()),
                    input_hash: row.get(6)?,
                    detail: row.get(7)?,
                })
            })
            .map_err(|e| LedgerError(e.to_string()))?
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| LedgerError(e.to_string()))?;

        Ok(entries)
    }

    pub fn path(&self) -> &PathBuf {
        &self.db_path
    }
}

fn event_type_str(e: &AuditEventType) -> &'static str {
    match e {
        AuditEventType::ToolCall => "TOOL_CALL",
        AuditEventType::SecurityViolation => "SECURITY_VIOLATION",
        AuditEventType::CompactionTriggered => "COMPACTION_TRIGGERED",
        AuditEventType::MemoryUpsert => "MEMORY_UPSERT",
        AuditEventType::SessionCreated => "SESSION_CREATED",
        AuditEventType::SessionExpired => "SESSION_EXPIRED",
        AuditEventType::PermissionGranted => "PERMISSION_GRANTED",
        AuditEventType::PermissionDenied => "PERMISSION_DENIED",
        AuditEventType::RollbackExecuted => "ROLLBACK_EXECUTED",
    }
}

fn outcome_str(o: &AuditOutcome) -> &'static str {
    match o {
        AuditOutcome::Success => "SUCCESS",
        AuditOutcome::Failure => "FAILURE",
        AuditOutcome::Blocked => "BLOCKED",
        AuditOutcome::Quarantined => "QUARANTINED",
    }
}

fn parse_event_type(s: &str) -> AuditEventType {
    match s {
        "SECURITY_VIOLATION" => AuditEventType::SecurityViolation,
        "COMPACTION_TRIGGERED" => AuditEventType::CompactionTriggered,
        "MEMORY_UPSERT" => AuditEventType::MemoryUpsert,
        "SESSION_CREATED" => AuditEventType::SessionCreated,
        "SESSION_EXPIRED" => AuditEventType::SessionExpired,
        "PERMISSION_GRANTED" => AuditEventType::PermissionGranted,
        "PERMISSION_DENIED" => AuditEventType::PermissionDenied,
        "ROLLBACK_EXECUTED" => AuditEventType::RollbackExecuted,
        _ => AuditEventType::ToolCall,
    }
}

fn parse_outcome(s: &str) -> AuditOutcome {
    match s {
        "SUCCESS" => AuditOutcome::Success,
        "BLOCKED" => AuditOutcome::Blocked,
        "QUARANTINED" => AuditOutcome::Quarantined,
        _ => AuditOutcome::Failure,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn temp_ledger() -> WormLedger {
        let dir = tempdir().unwrap();
        let path = dir.into_path().join("test_audit.db");
        WormLedger::open(path).unwrap()
    }

    #[test]
    fn test_open_creates_db() {
        let ledger = temp_ledger();
        assert!(ledger.path().exists());
    }

    #[test]
    fn test_append_blocking_returns_seq() {
        let ledger = temp_ledger();
        let seq = ledger
            .append_blocking(
                "sess-1",
                &AuditEventType::ToolCall,
                "bash",
                &AuditOutcome::Success,
                "abc123hash",
                "cargo build",
            )
            .unwrap();
        assert_eq!(seq, 1);
    }

    #[test]
    fn test_seq_increments() {
        let ledger = temp_ledger();
        let s1 = ledger
            .append_blocking(
                "s",
                &AuditEventType::ToolCall,
                "bash",
                &AuditOutcome::Success,
                "h1",
                "d1",
            )
            .unwrap();
        let s2 = ledger
            .append_blocking(
                "s",
                &AuditEventType::ToolCall,
                "bash",
                &AuditOutcome::Success,
                "h2",
                "d2",
            )
            .unwrap();
        assert!(s2 > s1);
    }

    #[test]
    fn test_verify_chain_intact() {
        let ledger = temp_ledger();
        ledger
            .append_blocking(
                "s",
                &AuditEventType::SessionCreated,
                "system",
                &AuditOutcome::Success,
                "hash1",
                "created",
            )
            .unwrap();
        ledger
            .append_blocking(
                "s",
                &AuditEventType::ToolCall,
                "bash",
                &AuditOutcome::Success,
                "hash2",
                "tool",
            )
            .unwrap();
        let tampered_at = ledger.verify_chain().unwrap();
        assert!(tampered_at.is_none(), "Chain must be intact");
    }

    #[test]
    fn test_get_entries_returns_correct_count() {
        let ledger = temp_ledger();
        for i in 0..5 {
            ledger
                .append_blocking(
                    "sess-x",
                    &AuditEventType::ToolCall,
                    "bash",
                    &AuditOutcome::Success,
                    &format!("hash{i}"),
                    &format!("detail{i}"),
                )
                .unwrap();
        }
        let entries = ledger.get_entries("sess-x", 3).unwrap();
        assert_eq!(entries.len(), 3);
    }

    #[test]
    fn test_get_entries_filters_by_session() {
        let ledger = temp_ledger();
        ledger
            .append_blocking(
                "sess-a",
                &AuditEventType::ToolCall,
                "bash",
                &AuditOutcome::Success,
                "h1",
                "d1",
            )
            .unwrap();
        ledger
            .append_blocking(
                "sess-b",
                &AuditEventType::ToolCall,
                "bash",
                &AuditOutcome::Success,
                "h2",
                "d2",
            )
            .unwrap();
        let entries = ledger.get_entries("sess-a", 10).unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].session_id, "sess-a");
    }

    #[test]
    fn test_empty_ledger_verify_chain_ok() {
        let ledger = temp_ledger();
        let result = ledger.verify_chain().unwrap();
        assert!(result.is_none());
    }
}
