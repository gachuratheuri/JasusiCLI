use serde::{Deserialize, Serialize};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionMeta {
    pub session_id: String,
    pub project: String,
    pub created_at: String,
    pub updated_at: String,
    pub input_tokens: u64,
    pub output_tokens: u64,
    pub compaction_count: u32,
    pub turn_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContentBlock {
    pub block_type: ContentBlockType,
    pub content: String,
    pub is_error: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum ContentBlockType {
    Text,
    ToolUse,
    ToolResult,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TranscriptEntry {
    pub role: String,
    pub content: Vec<ContentBlock>,
    pub timestamp: String,
    pub turn_seq: u32,
}

#[derive(Debug)]
pub struct StoreError(pub String);

impl std::fmt::Display for StoreError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "StoreError: {}", self.0)
    }
}

impl std::error::Error for StoreError {}

pub struct SessionStore {
    base_dir: PathBuf,
    index: Arc<Mutex<std::collections::HashMap<String, SessionMeta>>>,
}

impl SessionStore {
    pub fn open(base_dir: PathBuf) -> Result<Self, StoreError> {
        std::fs::create_dir_all(&base_dir).map_err(|e| StoreError(e.to_string()))?;

        let index = Self::load_index(&base_dir)?;
        Ok(Self {
            base_dir,
            index: Arc::new(Mutex::new(index)),
        })
    }

    pub fn default_path() -> PathBuf {
        dirs::home_dir()
            .unwrap_or_else(|| PathBuf::from("."))
            .join(".jasusi")
            .join("sessions")
    }

    fn index_path(base_dir: &Path) -> PathBuf {
        base_dir.join("sessions.json")
    }

    fn transcript_path(&self, session_id: &str) -> PathBuf {
        self.base_dir.join(format!("{session_id}.jsonl"))
    }

    fn load_index(
        base_dir: &Path,
    ) -> Result<std::collections::HashMap<String, SessionMeta>, StoreError> {
        let path = Self::index_path(base_dir);
        if !path.exists() {
            return Ok(std::collections::HashMap::new());
        }
        let data = std::fs::read_to_string(&path).map_err(|e| StoreError(e.to_string()))?;
        serde_json::from_str(&data).map_err(|e| StoreError(e.to_string()))
    }

    fn flush_index(
        base_dir: &Path,
        index: &std::collections::HashMap<String, SessionMeta>,
    ) -> Result<(), StoreError> {
        let final_path = Self::index_path(base_dir);
        let tmp_path = base_dir.join("sessions.json.tmp");

        let data =
            serde_json::to_string_pretty(index).map_err(|e| StoreError(e.to_string()))?;

        std::fs::write(&tmp_path, data.as_bytes()).map_err(|e| StoreError(e.to_string()))?;

        std::fs::rename(&tmp_path, &final_path).map_err(|e| StoreError(e.to_string()))?;

        Ok(())
    }

    pub fn create_session(
        &self,
        session_id: impl Into<String>,
        project: impl Into<String>,
    ) -> Result<SessionMeta, StoreError> {
        let now = chrono::Utc::now().to_rfc3339();
        let meta = SessionMeta {
            session_id: session_id.into(),
            project: project.into(),
            created_at: now.clone(),
            updated_at: now,
            input_tokens: 0,
            output_tokens: 0,
            compaction_count: 0,
            turn_count: 0,
        };

        let mut index = self.index.lock().map_err(|e| StoreError(e.to_string()))?;
        index.insert(meta.session_id.clone(), meta.clone());
        Self::flush_index(&self.base_dir, &index)?;
        Ok(meta)
    }

    pub fn get_session(&self, session_id: &str) -> Option<SessionMeta> {
        self.index.lock().ok()?.get(session_id).cloned()
    }

    pub fn update_tokens(
        &self,
        session_id: &str,
        input_tokens: u64,
        output_tokens: u64,
    ) -> Result<(), StoreError> {
        let mut index = self.index.lock().map_err(|e| StoreError(e.to_string()))?;
        if let Some(meta) = index.get_mut(session_id) {
            meta.input_tokens += input_tokens;
            meta.output_tokens += output_tokens;
            meta.turn_count += 1;
            meta.updated_at = chrono::Utc::now().to_rfc3339();
        }
        Self::flush_index(&self.base_dir, &index)
    }

    pub fn increment_compaction(&self, session_id: &str) -> Result<(), StoreError> {
        let mut index = self.index.lock().map_err(|e| StoreError(e.to_string()))?;
        if let Some(meta) = index.get_mut(session_id) {
            meta.compaction_count += 1;
            meta.updated_at = chrono::Utc::now().to_rfc3339();
        }
        Self::flush_index(&self.base_dir, &index)
    }

    pub fn append_transcript(
        &self,
        session_id: &str,
        entry: &TranscriptEntry,
    ) -> Result<(), StoreError> {
        let path = self.transcript_path(session_id);
        let line = serde_json::to_string(entry).map_err(|e| StoreError(e.to_string()))?;

        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .map_err(|e| StoreError(e.to_string()))?;

        writeln!(file, "{line}").map_err(|e| StoreError(e.to_string()))?;

        Ok(())
    }

    pub fn read_transcript(
        &self,
        session_id: &str,
        limit: usize,
    ) -> Result<Vec<TranscriptEntry>, StoreError> {
        let path = self.transcript_path(session_id);
        if !path.exists() {
            return Ok(vec![]);
        }

        let data = std::fs::read_to_string(&path).map_err(|e| StoreError(e.to_string()))?;

        let mut entries: Vec<TranscriptEntry> = data
            .lines()
            .filter(|l| !l.trim().is_empty())
            .filter_map(|line| serde_json::from_str(line).ok())
            .collect();

        if entries.len() > limit {
            entries.drain(..entries.len() - limit);
        }
        Ok(entries)
    }

    pub fn list_sessions(&self) -> Vec<SessionMeta> {
        self.index
            .lock()
            .map(|idx| idx.values().cloned().collect())
            .unwrap_or_default()
    }

    pub fn prune(&self, max_age_days: u32, max_entries: usize) -> Result<usize, StoreError> {
        let cutoff =
            chrono::Utc::now() - chrono::Duration::days(i64::from(max_age_days));
        let cutoff_str = cutoff.to_rfc3339();

        let mut index = self.index.lock().map_err(|e| StoreError(e.to_string()))?;
        let before = index.len();

        index.retain(|_, meta| meta.updated_at > cutoff_str);

        if index.len() > max_entries {
            let mut sorted: Vec<(String, String)> = index
                .iter()
                .map(|(k, v)| (k.clone(), v.updated_at.clone()))
                .collect();
            sorted.sort_by(|a, b| a.1.cmp(&b.1));
            let to_remove = index.len() - max_entries;
            for (id, _) in sorted.iter().take(to_remove) {
                index.remove(id);
                let _ = std::fs::remove_file(self.transcript_path(id));
            }
        }

        let pruned = before.saturating_sub(index.len());
        Self::flush_index(&self.base_dir, &index)?;
        Ok(pruned)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    fn temp_store() -> SessionStore {
        let dir = tempdir().unwrap().into_path();
        SessionStore::open(dir).unwrap()
    }

    #[test]
    fn test_create_and_get_session() {
        let store = temp_store();
        store.create_session("sess-1", "my-project").unwrap();
        let meta = store.get_session("sess-1").unwrap();
        assert_eq!(meta.project, "my-project");
        assert_eq!(meta.compaction_count, 0);
    }

    #[test]
    fn test_sessions_json_is_written_atomically() {
        let dir = tempdir().unwrap().into_path();
        let store = SessionStore::open(dir.clone()).unwrap();
        store.create_session("atomic-test", "proj").unwrap();
        let path = dir.join("sessions.json");
        assert!(path.exists());
        assert!(!dir.join("sessions.json.tmp").exists());
    }

    #[test]
    fn test_update_tokens_accumulates() {
        let store = temp_store();
        store.create_session("tok", "proj").unwrap();
        store.update_tokens("tok", 100, 50).unwrap();
        store.update_tokens("tok", 200, 100).unwrap();
        let meta = store.get_session("tok").unwrap();
        assert_eq!(meta.input_tokens, 300);
        assert_eq!(meta.output_tokens, 150);
        assert_eq!(meta.turn_count, 2);
    }

    #[test]
    fn test_increment_compaction() {
        let store = temp_store();
        store.create_session("comp", "proj").unwrap();
        store.increment_compaction("comp").unwrap();
        store.increment_compaction("comp").unwrap();
        let meta = store.get_session("comp").unwrap();
        assert_eq!(meta.compaction_count, 2);
    }

    #[test]
    fn test_append_and_read_transcript() {
        let store = temp_store();
        store.create_session("t1", "proj").unwrap();
        let entry = TranscriptEntry {
            role: "user".to_string(),
            content: vec![ContentBlock {
                block_type: ContentBlockType::Text,
                content: "Hello".to_string(),
                is_error: false,
            }],
            timestamp: chrono::Utc::now().to_rfc3339(),
            turn_seq: 1,
        };
        store.append_transcript("t1", &entry).unwrap();
        let entries = store.read_transcript("t1", 10).unwrap();
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].role, "user");
    }

    #[test]
    fn test_read_transcript_limit() {
        let store = temp_store();
        store.create_session("lim", "proj").unwrap();
        for i in 0..10u32 {
            let entry = TranscriptEntry {
                role: "user".to_string(),
                content: vec![],
                timestamp: chrono::Utc::now().to_rfc3339(),
                turn_seq: i,
            };
            store.append_transcript("lim", &entry).unwrap();
        }
        let entries = store.read_transcript("lim", 3).unwrap();
        assert_eq!(entries.len(), 3);
    }

    #[test]
    fn test_prune_by_max_entries() {
        let store = temp_store();
        for i in 0..10 {
            store
                .create_session(&format!("s{i}"), "proj")
                .unwrap();
        }
        let pruned = store.prune(365, 5).unwrap();
        assert_eq!(pruned, 5);
        assert_eq!(store.list_sessions().len(), 5);
    }

    #[test]
    fn test_list_sessions() {
        let store = temp_store();
        store.create_session("a", "p").unwrap();
        store.create_session("b", "p").unwrap();
        assert_eq!(store.list_sessions().len(), 2);
    }
}
