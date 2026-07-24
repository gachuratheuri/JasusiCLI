use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, BufWriter, Seek, SeekFrom, Write};
use std::path::Path;
use std::sync::Mutex;

use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::id::CorrelationId;

/// A structured, redacted audit event with correlation context.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEvent {
    #[serde(flatten)]
    pub header: AuditHeader,
    pub actor: String,
    pub action: String,
    pub resource: String,
    pub outcome: AuditOutcome,
    pub detail: Value,
    pub previous_hash: String,
    pub entry_hash: String,
    #[serde(skip_serializing_if = "Vec::is_empty", default)]
    pub redacted_fields: Vec<String>,
}

/// Fixed fields for every audit entry.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditHeader {
    pub seq: u64,
    pub timestamp: String,
    pub correlation_id: String,
    pub service_name: String,
    pub version: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum AuditOutcome {
    Success,
    Failure,
    Denied,
    Cancelled,
}

/// Redacts common secrets, tokens, and long payloads from values before
/// serialization.
#[derive(Debug, Clone, Default)]
pub struct Redactor {
    patterns: Vec<(regex::Regex, String)>,
}

impl Redactor {
    /// Builds the default redactor with secret-like patterns.
    #[must_use]
    pub fn default_patterns() -> Self {
        let patterns = vec![
            (
                regex::Regex::new(
                    r"(?i)(api_?key|token|secret|password|bearer)\s*[:=]\s*[^\s&]{4,}",
                )
                .unwrap(),
                "${1}: [REACTED]".to_string(),
            ),
            (
                regex::Regex::new(r"(?i)Authorization\s*:\s*Bearer\s+[^\s]+").unwrap(),
                "Authorization: Bearer [REDACTED]".to_string(),
            ),
            (
                regex::Regex::new(r"[A-Za-z0-9_-]{40,}").unwrap(),
                "[REDACTED_LONG_TOKEN]".to_string(),
            ),
        ];
        Self { patterns }
    }

    /// Redacts a plain string in place.
    #[must_use]
    pub fn redact_text(&self, input: &str) -> String {
        let mut out = input.to_string();
        for (re, repl) in &self.patterns {
            out = re.replace_all(&out, repl.as_str()).to_string();
        }
        out
    }

    /// Redacts a [`serde_json::Value`] recursively, returning a new value.
    #[must_use]
    pub fn redact_value(&self, value: &Value) -> Value {
        match value {
            Value::String(s) => {
                if is_sensitive_key(s) {
                    Value::String("[REDACTED]".to_string())
                } else {
                    Value::String(self.redact_text(s))
                }
            }
            Value::Object(map) => {
                let mut out = serde_json::Map::new();
                for (k, v) in map {
                    let key_lower = k.to_lowercase();
                    let redacted = if SENSITIVE_KEYS.iter().any(|s| key_lower.contains(s)) {
                        Value::String("[REDACTED]".to_string())
                    } else {
                        self.redact_value(v)
                    };
                    out.insert(k.clone(), redacted);
                }
                Value::Object(out)
            }
            Value::Array(arr) => Value::Array(arr.iter().map(|v| self.redact_value(v)).collect()),
            other => other.clone(),
        }
    }
}

const SENSITIVE_KEYS: &[&str] = &[
    "api_key",
    "apikey",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "private_key",
    "access_token",
    "refresh_token",
];

fn is_sensitive_key(s: &str) -> bool {
    let lower = s.to_lowercase();
    SENSITIVE_KEYS.iter().any(|k| lower.contains(k))
}

/// Append-only structured audit log with sequence numbers and correlation IDs.
#[derive(Debug)]
pub struct AuditLog {
    writer: Mutex<BufWriter<File>>,
    sequence: Mutex<u64>,
    previous_hash: Mutex<String>,
    redactor: Redactor,
    service_name: String,
    version: String,
}

impl AuditLog {
    /// Opens (or creates) an append-only JSONL audit log.
    ///
    /// # Errors
    ///
    /// Returns an error if the log file cannot be created or opened.
    pub fn open(
        path: impl AsRef<Path>,
        service_name: String,
        version: String,
    ) -> Result<Self, std::io::Error> {
        let path = path.as_ref();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .append(true)
            .open(path)?;
        let mut next_sequence = 0;
        let mut previous_hash = String::new();
        let reader = BufReader::new(file.try_clone()?);
        for line in reader.lines() {
            let line = line?;
            if line.trim().is_empty() {
                continue;
            }
            let event: AuditEvent = serde_json::from_str(&line)
                .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))?;
            if event.header.seq >= next_sequence {
                next_sequence = event.header.seq.saturating_add(1);
                previous_hash = event.entry_hash;
            }
        }
        Ok(Self {
            writer: Mutex::new(BufWriter::new(file)),
            sequence: Mutex::new(next_sequence),
            previous_hash: Mutex::new(previous_hash),
            redactor: Redactor::default_patterns(),
            service_name,
            version,
        })
    }

    /// Appends an audit event, redacting `detail` and recording which fields
    /// were transformed.
    ///
    /// # Errors
    ///
    /// Returns an error if serialization or writing fails.
    pub fn append(
        &self,
        correlation_id: &CorrelationId,
        actor: impl Into<String>,
        action: impl Into<String>,
        resource: impl Into<String>,
        outcome: AuditOutcome,
        detail: &Value,
    ) -> Result<u64, std::io::Error> {
        let redacted = self.redactor.redact_value(detail);
        let mut redacted_fields = Vec::new();
        if redacted != *detail {
            redacted_fields.push("detail".to_string());
        }

        let mut seq_guard = self
            .sequence
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let seq = *seq_guard;
        *seq_guard += 1;
        let mut previous_hash_guard = self
            .previous_hash
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);

        let mut event = AuditEvent {
            header: AuditHeader {
                seq,
                timestamp: Utc::now().to_rfc3339(),
                correlation_id: correlation_id.to_string(),
                service_name: self.service_name.clone(),
                version: self.version.clone(),
            },
            actor: actor.into(),
            action: action.into(),
            resource: resource.into(),
            outcome,
            detail: redacted,
            previous_hash: previous_hash_guard.clone(),
            entry_hash: String::new(),
            redacted_fields,
        };

        let canonical = serde_json::to_vec(&event)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        let digest = Sha256::digest([previous_hash_guard.as_bytes(), &canonical].concat());
        event.entry_hash = hex::encode(digest);
        let line = serde_json::to_string(&event)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;

        let mut writer = self
            .writer
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        writeln!(writer, "{line}")?;
        writer.flush()?;
        *previous_hash_guard = event.entry_hash;
        Ok(seq)
    }

    /// Verifies sequence continuity and the hash chain from disk.
    pub fn verify(&self) -> Result<(), String> {
        let path = {
            let writer = self.writer.lock().map_err(|_| "audit writer poisoned")?;
            let mut file = writer.get_ref().try_clone().map_err(|e| e.to_string())?;
            file.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
            file
        };
        let reader = BufReader::new(path);
        let mut expected_seq: u64 = 0;
        let mut previous_hash = String::new();
        for line in reader.lines() {
            let line = line.map_err(|e| e.to_string())?;
            if line.trim().is_empty() {
                continue;
            }
            let event: AuditEvent = serde_json::from_str(&line).map_err(|e| e.to_string())?;
            if event.header.seq != expected_seq || event.previous_hash != previous_hash {
                return Err(format!(
                    "audit chain discontinuity at sequence {}",
                    event.header.seq
                ));
            }
            let supplied_hash = event.entry_hash.clone();
            let mut unsigned = event;
            unsigned.entry_hash.clear();
            let canonical = serde_json::to_vec(&unsigned).map_err(|e| e.to_string())?;
            let digest = Sha256::digest([previous_hash.as_bytes(), &canonical].concat());
            if supplied_hash != hex::encode(digest) {
                return Err(format!("audit hash mismatch at sequence {expected_seq}"));
            }
            previous_hash = supplied_hash;
            expected_seq = expected_seq.saturating_add(1);
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redactor_masks_secrets_and_long_tokens() {
        let r = Redactor::default_patterns();
        let out = r.redact_text("api_key: super_secret_token_abc123");
        assert!(!out.contains("super_secret_token_abc123"));
        assert!(out.contains("[REACTED]"));
    }

    #[test]
    fn redactor_masks_json_sensitive_keys() {
        let r = Redactor::default_patterns();
        let value = serde_json::json!({
            "user": "alice",
            "api_key": "should_not_leak",
            "nested": { "password": "hunter2" },
            "data": ["Authorization: Bearer xyz"]
        });
        let redacted = r.redact_value(&value);
        assert_eq!(redacted["api_key"], "[REDACTED]");
        assert_eq!(redacted["nested"]["password"], "[REDACTED]");
        let data = redacted["data"].as_array().unwrap()[0].as_str().unwrap();
        assert!(data.contains("[REDACTED]"));
    }

    #[test]
    fn audit_log_appends_sequenced_events() {
        let dir = tempfile::tempdir().unwrap();
        let log = AuditLog::open(
            dir.path().join("audit.jsonl"),
            "test".to_string(),
            "0.0.0".to_string(),
        )
        .unwrap();

        let cid = CorrelationId::parse("corr-123").unwrap();
        let seq1 = log
            .append(
                &cid,
                "alice",
                "submit_job",
                "/api/jobs",
                AuditOutcome::Success,
                &serde_json::json!({"api_key": "secret"}),
            )
            .unwrap();
        let seq2 = log
            .append(
                &cid,
                "alice",
                "cancel_job",
                "/api/jobs/1",
                AuditOutcome::Cancelled,
                &serde_json::json!({}),
            )
            .unwrap();

        assert_eq!(seq1, 0);
        assert_eq!(seq2, 1);

        let contents = std::fs::read_to_string(dir.path().join("audit.jsonl")).unwrap();
        assert!(contents.contains("\"seq\":0"));
        assert!(contents.contains("\"seq\":1"));
        assert!(contents.contains("[REDACTED]"));
        assert!(contents.contains("corr-123"));
        assert!(log.verify().is_ok());
    }

    #[test]
    fn audit_sequence_and_hash_chain_survive_reopen() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("audit.jsonl");
        let cid = CorrelationId::parse("corr-123").unwrap();
        {
            let log = AuditLog::open(&path, "test".to_string(), "0.0.0".to_string()).unwrap();
            log.append(
                &cid,
                "alice",
                "one",
                "/",
                AuditOutcome::Success,
                &Value::Null,
            )
            .unwrap();
        }
        let log = AuditLog::open(&path, "test".to_string(), "0.0.0".to_string()).unwrap();
        assert_eq!(
            log.append(
                &cid,
                "alice",
                "two",
                "/",
                AuditOutcome::Success,
                &Value::Null
            )
            .unwrap(),
            1
        );
        assert!(log.verify().is_ok());
        drop(log);

        let mut text = std::fs::read_to_string(&path).unwrap();
        text = text.replacen("\"two\"", "\"tampered\"", 1);
        std::fs::write(&path, text).unwrap();
        let reopened = AuditLog::open(&path, "test".to_string(), "0.0.0".to_string()).unwrap();
        assert!(reopened.verify().is_err());
    }
}
