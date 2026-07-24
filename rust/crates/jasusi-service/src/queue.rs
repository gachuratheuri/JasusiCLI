use std::collections::HashMap;
use std::fmt;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use rusqlite::{params, Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::{broadcast, mpsc, Mutex, Semaphore};
use tokio_util::sync::CancellationToken;

use crate::id::{IdempotencyKey, JobId};
use crate::profile::ServiceConfig;

/// The lifecycle state of a queued job.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum JobState {
    Pending,
    Running,
    Completed,
    Failed,
    Cancelled,
}

impl fmt::Display for JobState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Pending => write!(f, "PENDING"),
            Self::Running => write!(f, "RUNNING"),
            Self::Completed => write!(f, "COMPLETED"),
            Self::Failed => write!(f, "FAILED"),
            Self::Cancelled => write!(f, "CANCELLED"),
        }
    }
}

/// A single work item submitted to the durable queue.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Job {
    pub id: JobId,
    pub idempotency_key: IdempotencyKey,
    pub project_id: String,
    pub user_id: String,
    pub prompt: String,
    pub requested_profile: String,
    pub state: JobState,
    pub created_at_ms: u64,
    pub started_at_ms: Option<u64>,
    pub completed_at_ms: Option<u64>,
    pub terminal_message: Option<String>,
    pub worker_id: Option<String>,
}

/// A job event streamed to observers and persisted for audit/replay.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobEvent {
    pub job_id: String,
    pub event_type: String,
    pub payload: Value,
    pub emitted_at_ms: u64,
}

impl JobEvent {
    #[must_use]
    pub fn new(job_id: impl Into<String>, event_type: impl Into<String>, payload: Value) -> Self {
        Self {
            job_id: job_id.into(),
            event_type: event_type.into(),
            payload,
            emitted_at_ms: now_ms(),
        }
    }
}

/// Errors returned by the job queue.
#[derive(Debug, thiserror::Error)]
pub enum QueueError {
    #[error("database error: {0}")]
    Database(#[from] rusqlite::Error),
    #[error("serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("backpressure: queue is at capacity ({current}/{capacity}); retry after {retry_after_seconds}s")]
    Backpressure {
        current: usize,
        capacity: usize,
        retry_after_seconds: u64,
    },
    #[error("job {0} not found")]
    NotFound(JobId),
    #[error("job {0} already in terminal state {1}")]
    AlreadyTerminal(JobId, String),
    #[error("invalid idempotency key: {0}")]
    BadIdempotency(String),
    #[error("invalid job id: {0}")]
    BadJobId(String),
    #[error("processor error: {0}")]
    Processor(String),
    #[error("queue is draining; no new jobs accepted")]
    Draining,
    #[error("input field '{field}' exceeds the configured limit of {limit} bytes")]
    InputTooLarge { field: &'static str, limit: usize },
    #[error("job event exceeds the configured limit of {limit} bytes")]
    EventTooLarge { limit: usize },
}

/// Trait implemented by the engine-specific executor attached to the queue.
#[async_trait::async_trait]
pub trait JobProcessor: Send + Sync + 'static {
    /// Processes one job. Implementations should emit [`JobEvent`]s through the
    /// supplied channel and return a terminal message.
    async fn process(
        &self,
        job: &Job,
        events: mpsc::Sender<JobEvent>,
        cancellation: CancellationToken,
    ) -> std::result::Result<String, QueueError>;
}

enum Submission {
    Existing(String),
    Inserted(JobId),
}

/// Durable, bounded, cancel-aware job queue with worker pool and event streams.
pub struct JobQueue {
    conn: Arc<Mutex<Connection>>,
    max_queue_size: usize,
    max_concurrent: usize,
    max_prompt_bytes: usize,
    max_event_bytes: usize,
    concurrency: Arc<Semaphore>,
    channels: Arc<Mutex<HashMap<String, broadcast::Sender<JobEvent>>>>,
    cancellations: Arc<Mutex<HashMap<String, CancellationToken>>>,
    drain: Arc<std::sync::atomic::AtomicBool>,
}

impl std::fmt::Debug for JobQueue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("JobQueue")
            .field("max_queue_size", &self.max_queue_size)
            .field("max_concurrent", &self.max_concurrent)
            .finish_non_exhaustive()
    }
}

impl JobQueue {
    /// Opens or creates the queue database at the configured path.
    ///
    /// # Errors
    ///
    /// Returns an error if the database cannot be created or migrated.
    pub fn open(config: &ServiceConfig) -> Result<Self, QueueError> {
        let path = &config.queue_db_path;
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(path)?;
        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=FULL;
             CREATE TABLE IF NOT EXISTS jobs (
                 id                  TEXT PRIMARY KEY,
                 idempotency_key     TEXT NOT NULL UNIQUE,
                 project_id          TEXT NOT NULL,
                 user_id             TEXT NOT NULL,
                 prompt              TEXT NOT NULL,
                 requested_profile   TEXT NOT NULL,
                 state               TEXT NOT NULL,
                 created_at_ms       INTEGER NOT NULL,
                 started_at_ms       INTEGER,
                 completed_at_ms     INTEGER,
                 terminal_message    TEXT,
                 worker_id           TEXT
             );
             CREATE INDEX IF NOT EXISTS idx_jobs_state_created
                 ON jobs(state, created_at_ms);
             CREATE INDEX IF NOT EXISTS idx_jobs_idempotency
                 ON jobs(idempotency_key);
             CREATE TABLE IF NOT EXISTS job_events (
                 id          INTEGER PRIMARY KEY AUTOINCREMENT,
                 job_id      TEXT NOT NULL,
                 event_type  TEXT NOT NULL,
                 payload_json TEXT NOT NULL,
                 emitted_at_ms INTEGER NOT NULL,
                 FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
             );
             CREATE INDEX IF NOT EXISTS idx_events_job_id
                 ON job_events(job_id, emitted_at_ms);",
        )?;

        Ok(Self {
            conn: Arc::new(Mutex::new(conn)),
            max_queue_size: config.max_queue_size,
            max_concurrent: config.max_concurrent_jobs,
            max_prompt_bytes: config.max_prompt_bytes,
            max_event_bytes: config.max_event_bytes,
            concurrency: Arc::new(Semaphore::new(config.max_concurrent_jobs)),
            channels: Arc::new(Mutex::new(HashMap::new())),
            cancellations: Arc::new(Mutex::new(HashMap::new())),
            drain: Arc::new(std::sync::atomic::AtomicBool::new(false)),
        })
    }

    /// Submits a job to the queue, respecting idempotency, capacity, and drain
    /// state.
    ///
    /// # Errors
    ///
    /// Returns `Backpressure` when the queue is full, `Draining` when draining,
    /// or `Database` on persistence failures.
    pub async fn submit(
        &self,
        idempotency_key: IdempotencyKey,
        project_id: String,
        user_id: String,
        prompt: String,
        requested_profile: String,
    ) -> Result<Job, QueueError> {
        if self.drain.load(std::sync::atomic::Ordering::Relaxed) {
            return Err(QueueError::Draining);
        }

        for (field, value, limit) in [
            ("project_id", project_id.as_str(), 256),
            ("user_id", user_id.as_str(), 256),
            ("requested_profile", requested_profile.as_str(), 64),
        ] {
            if value.len() > limit {
                return Err(QueueError::InputTooLarge { field, limit });
            }
        }
        if prompt.len() > self.max_prompt_bytes {
            return Err(QueueError::InputTooLarge {
                field: "prompt",
                limit: self.max_prompt_bytes,
            });
        }

        let capacity = self.max_queue_size;
        // Keep the capacity check and insert under one SQLite connection
        // mutex.  A separate count followed by an insert allows concurrent
        // callers to oversubscribe the bounded queue.
        let submission = {
            let mut conn = self.conn.lock().await;
            let tx = conn.transaction()?;
            // Idempotent retries must succeed even while the queue is full;
            // they do not create additional work or consume capacity.
            if let Some(existing_id) = tx
                .query_row(
                    "SELECT id FROM jobs WHERE idempotency_key = ?1",
                    params![idempotency_key.as_str()],
                    |row| row.get::<_, String>(0),
                )
                .optional()?
            {
                tx.commit()?;
                Submission::Existing(existing_id)
            } else {
                let current: i64 = tx.query_row(
                    "SELECT COUNT(*) FROM jobs WHERE state IN ('PENDING','RUNNING')",
                    [],
                    |row| row.get(0),
                )?;
                let current = usize::try_from(current).unwrap_or(usize::MAX);
                if current >= capacity {
                    return Err(QueueError::Backpressure {
                        current,
                        capacity,
                        retry_after_seconds: 1,
                    });
                }

                let id = JobId::generate();
                let now = now_ms();
                tx.execute(
                    "INSERT INTO jobs
                     (id, idempotency_key, project_id, user_id, prompt, requested_profile, state, created_at_ms)
                     VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8)",
                    params![
                        id.to_string(),
                        idempotency_key.as_str(),
                        project_id,
                        user_id,
                        prompt,
                        requested_profile,
                        JobState::Pending.to_string(),
                        now,
                    ],
                )?;
                tx.commit()?;
                Submission::Inserted(id)
            }
        };

        match submission {
            Submission::Existing(existing_id) => self
                .get(&existing_id)
                .await?
                .ok_or(QueueError::BadJobId(existing_id)),
            Submission::Inserted(id) => {
                self.emit(
                    &id,
                    JobEvent::new(id.to_string(), "submitted", serde_json::json!({})),
                )
                .await;
                self.get(&id.to_string())
                    .await?
                    .ok_or(QueueError::BadJobId(id.to_string()))
            }
        }
    }

    /// Retrieves a job by id, including terminal message.
    ///
    /// # Errors
    ///
    /// Returns a database error if the query fails.
    pub async fn get(&self, id: &str) -> Result<Option<Job>, QueueError> {
        let conn = self.conn.lock().await;
        let mut stmt = conn.prepare(
            "SELECT id, idempotency_key, project_id, user_id, prompt, requested_profile,
                    state, created_at_ms, started_at_ms, completed_at_ms, terminal_message, worker_id
             FROM jobs WHERE id = ?1",
        )?;
        let mut rows = stmt.query(params![id])?;
        if let Some(row) = rows.next()? {
            Ok(Some(row_to_job(row)?))
        } else {
            Ok(None)
        }
    }

    /// Returns the job associated with an idempotency key, if one exists.
    pub async fn find_by_idempotency(
        &self,
        key: &IdempotencyKey,
    ) -> Result<Option<Job>, QueueError> {
        let id = {
            let conn = self.conn.lock().await;
            conn.query_row(
                "SELECT id FROM jobs WHERE idempotency_key = ?1",
                params![key.as_str()],
                |row| row.get::<_, String>(0),
            )
            .optional()?
        };
        match id {
            Some(id) => self.get(&id).await,
            None => Ok(None),
        }
    }

    /// Cancels a pending or running job.
    ///
    /// # Errors
    ///
    /// Returns `NotFound` or `AlreadyTerminal` as appropriate.
    pub async fn cancel(&self, id: &str) -> Result<Job, QueueError> {
        let parsed_id = JobId::parse(id).map_err(QueueError::BadJobId)?;
        let job = self.get(id).await?.ok_or(QueueError::NotFound(parsed_id))?;
        if matches!(
            job.state,
            JobState::Completed | JobState::Failed | JobState::Cancelled
        ) {
            return Err(QueueError::AlreadyTerminal(
                job.id.clone(),
                job.state.to_string(),
            ));
        }

        if let Some(token) = self.cancellations.lock().await.get(id).cloned() {
            token.cancel();
        }
        let now = now_ms();
        let conn = self.conn.lock().await;
        conn.execute(
            "UPDATE jobs SET state = ?1, completed_at_ms = ?2, terminal_message = ?3 WHERE id = ?4 AND state IN ('PENDING','RUNNING')",
            params![JobState::Cancelled.to_string(), now, "cancelled by operator", id],
        )?;
        drop(conn);

        self.emit(
            &job.id,
            JobEvent::new(id, "cancelled", serde_json::json!({"by": "operator"})),
        )
        .await;
        self.get(id)
            .await?
            .ok_or_else(|| QueueError::BadJobId(id.to_string()))
    }

    /// Starts a single worker that consumes jobs and dispatches them to the
    /// supplied processor until the shutdown signal fires.
    pub async fn run_worker<P: JobProcessor>(
        self: Arc<Self>,
        worker_id: String,
        processor: Arc<P>,
        mut shutdown: tokio::sync::watch::Receiver<bool>,
    ) {
        loop {
            if *shutdown.borrow() {
                break;
            }
            let permit = tokio::select! {
                _ = shutdown.changed() => break,
                permit = self.concurrency.clone().acquire_owned() => match permit {
                    Ok(p) => p,
                    Err(_) => break,
                },
            };

            let job = match self.claim_next(&worker_id).await {
                Ok(Some(j)) => j,
                Ok(None) => {
                    drop(permit);
                    tokio::time::sleep(tokio::time::Duration::from_millis(250)).await;
                    continue;
                }
                Err(e) => {
                    tracing::error!(error = %e, worker_id = %worker_id, "queue claim failed");
                    drop(permit);
                    continue;
                }
            };

            let cancellation = CancellationToken::new();
            self.cancellations
                .lock()
                .await
                .insert(job.id.to_string(), cancellation.clone());

            let queue = self.clone();
            let processor = processor.clone();
            let worker_id = worker_id.clone();
            tokio::spawn(async move {
                let _permit = permit;
                let (events_tx, mut events_rx) = mpsc::channel::<JobEvent>(256);
                let persist = queue.clone();
                let persist_handle = tokio::spawn(async move {
                    while let Some(event) = events_rx.recv().await {
                        let _ = persist.persist_event(&event).await;
                        if let Some(sender) =
                            persist.channels.lock().await.get(&event.job_id).cloned()
                        {
                            let _ = sender.send(event.clone());
                        }
                    }
                });

                let result = processor.process(&job, events_tx, cancellation).await;
                let _ = persist_handle.await;

                let (state, message) = match result {
                    Ok(msg) => (JobState::Completed, msg),
                    Err(e) => (JobState::Failed, e.to_string()),
                };
                let _ = queue
                    .set_terminal(&job.id, state, &message, &worker_id)
                    .await;
            });
        }
    }

    /// Subscribes to a job's event stream. The receiver gets all future events
    /// plus historical ones if `from_start` is true.
    pub async fn subscribe(
        &self,
        id: &str,
        from_start: bool,
    ) -> Result<broadcast::Receiver<JobEvent>, QueueError> {
        let (tx, rx) = broadcast::channel(256);
        self.channels
            .lock()
            .await
            .insert(id.to_string(), tx.clone());

        if from_start {
            let conn = self.conn.lock().await;
            let mut stmt = conn.prepare(
                "SELECT job_id, event_type, payload_json, emitted_at_ms FROM job_events
                 WHERE job_id = ?1 ORDER BY emitted_at_ms ASC",
            )?;
            let mut rows = stmt.query(params![id])?;
            while let Some(row) = rows.next()? {
                let event = JobEvent {
                    job_id: row.get(0)?,
                    event_type: row.get(1)?,
                    payload: serde_json::from_str(&row.get::<_, String>(2)?).unwrap_or(Value::Null),
                    emitted_at_ms: row.get(3)?,
                };
                let _ = tx.send(event);
            }
        }
        Ok(rx)
    }

    /// Returns the number of pending and running jobs (queue depth).
    pub async fn active_count(&self) -> Result<usize, QueueError> {
        let conn = self.conn.lock().await;
        let count: i64 = conn.query_row(
            "SELECT COUNT(*) FROM jobs WHERE state IN ('PENDING','RUNNING')",
            [],
            |row| row.get(0),
        )?;
        Ok(usize::try_from(count).unwrap_or(usize::MAX))
    }

    /// Puts the queue into drain mode. Existing workers finish; new submissions
    /// are rejected.
    pub fn set_drain(&self, enable: bool) {
        self.drain
            .store(enable, std::sync::atomic::Ordering::Relaxed);
    }

    async fn claim_next(&self, worker_id: &str) -> Result<Option<Job>, QueueError> {
        let now = now_ms();
        let mut conn = self.conn.lock().await;
        let id = {
            let tx = conn.transaction()?;
            let id: Option<String> = {
                let mut stmt = tx.prepare(
                    "SELECT id FROM jobs WHERE state = 'PENDING' ORDER BY created_at_ms ASC LIMIT 1",
                )?;
                stmt.query_row([], |row| row.get(0)).optional()?
            };
            if let Some(ref id) = id {
                tx.execute(
                    "UPDATE jobs SET state = 'RUNNING', started_at_ms = ?1, worker_id = ?2 WHERE id = ?3",
                    params![now, worker_id, id],
                )?;
            }
            tx.commit()?;
            id
        };
        drop(conn);
        match id {
            Some(id) => self.get(&id).await,
            None => Ok(None),
        }
    }

    async fn set_terminal(
        &self,
        id: &JobId,
        state: JobState,
        message: &str,
        worker_id: &str,
    ) -> Result<(), QueueError> {
        let now = now_ms();
        let updated = {
            let conn = self.conn.lock().await;
            conn.execute(
                "UPDATE jobs SET state = ?1, completed_at_ms = ?2, terminal_message = ?3, worker_id = ?4 WHERE id = ?5 AND state = 'RUNNING'",
                params![state.to_string(), now, message, worker_id, id.to_string()],
            )?
        };
        self.cancellations.lock().await.remove(&id.to_string());
        if updated == 0 {
            // Cancellation won the race with the processor.  The cancellation
            // event is already terminal; do not append a contradictory worker
            // terminal event.
            return Ok(());
        }
        self.emit(
            id,
            JobEvent::new(
                id.to_string(),
                "terminal",
                serde_json::json!({"state": state.to_string(), "message": message}),
            ),
        )
        .await;
        Ok(())
    }

    async fn emit(&self, id: &JobId, event: JobEvent) {
        let _ = self.persist_event(&event).await;
        if let Some(sender) = self.channels.lock().await.get(&id.to_string()).cloned() {
            let _ = sender.send(event);
        }
    }

    async fn persist_event(&self, event: &JobEvent) -> Result<(), QueueError> {
        let payload_json = serde_json::to_string(&event.payload)?;
        if payload_json.len() > self.max_event_bytes {
            return Err(QueueError::EventTooLarge {
                limit: self.max_event_bytes,
            });
        }
        let conn = self.conn.lock().await;
        conn.execute(
            "INSERT INTO job_events (job_id, event_type, payload_json, emitted_at_ms) VALUES (?1, ?2, ?3, ?4)",
            params![event.job_id, event.event_type, payload_json, event.emitted_at_ms],
        )?;
        Ok(())
    }
}

fn row_to_job(row: &rusqlite::Row<'_>) -> std::result::Result<Job, QueueError> {
    Ok(Job {
        id: JobId::parse(row.get::<_, String>(0)?).map_err(QueueError::BadJobId)?,
        idempotency_key: IdempotencyKey::parse(row.get::<_, String>(1)?)
            .map_err(QueueError::BadJobId)?,
        project_id: row.get(2)?,
        user_id: row.get(3)?,
        prompt: row.get(4)?,
        requested_profile: row.get(5)?,
        state: match row.get::<_, String>(6)?.as_str() {
            "PENDING" => JobState::Pending,
            "RUNNING" => JobState::Running,
            "COMPLETED" => JobState::Completed,
            "FAILED" => JobState::Failed,
            "CANCELLED" => JobState::Cancelled,
            other => return Err(QueueError::BadJobId(format!("unknown state: {other}"))),
        },
        created_at_ms: row.get(7)?,
        started_at_ms: row.get(8)?,
        completed_at_ms: row.get(9)?,
        terminal_message: row.get(10)?,
        worker_id: row.get(11)?,
    })
}

fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
        .try_into()
        .unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[derive(Debug)]
    struct EchoProcessor;

    #[async_trait::async_trait]
    impl JobProcessor for EchoProcessor {
        async fn process(
            &self,
            job: &Job,
            events: mpsc::Sender<JobEvent>,
            _cancellation: CancellationToken,
        ) -> std::result::Result<String, QueueError> {
            let _ = events
                .send(JobEvent::new(
                    job.id.to_string(),
                    "progress",
                    serde_json::json!({"percent": 50}),
                ))
                .await;
            Ok(format!("echo: {}", job.prompt))
        }
    }

    fn test_config(dir: &TempDir) -> ServiceConfig {
        let mut cfg = ServiceConfig::local();
        cfg.queue_db_path = dir.path().join("queue.db");
        cfg.max_concurrent_jobs = 2;
        cfg.max_queue_size = 8;
        cfg
    }

    #[tokio::test]
    async fn submit_and_complete_job() {
        let dir = TempDir::new().unwrap();
        let queue = JobQueue::open(&test_config(&dir)).unwrap();

        let job = queue
            .submit(
                IdempotencyKey::new(),
                "demo".to_string(),
                "alice".to_string(),
                "hello".to_string(),
                "local".to_string(),
            )
            .await
            .unwrap();
        assert_eq!(job.state, JobState::Pending);

        let (shutdown_tx, shutdown_rx) = tokio::sync::watch::channel(false);
        let q = Arc::new(queue);
        let worker = tokio::spawn(q.clone().run_worker(
            "w1".to_string(),
            Arc::new(EchoProcessor),
            shutdown_rx,
        ));

        // Wait briefly for the worker to pick up and finish the job.
        tokio::time::timeout(tokio::time::Duration::from_secs(3), async {
            loop {
                let j = q.get(&job.id.to_string()).await.unwrap().unwrap();
                if matches!(j.state, JobState::Completed | JobState::Failed) {
                    break j;
                }
                tokio::time::sleep(tokio::time::Duration::from_millis(50)).await;
            }
        })
        .await
        .unwrap();

        let _ = shutdown_tx.send(true);
        let _ = worker.await;
    }

    #[tokio::test]
    async fn idempotency_returns_same_job() {
        let dir = TempDir::new().unwrap();
        let queue = JobQueue::open(&test_config(&dir)).unwrap();
        let key = IdempotencyKey::new();
        let job1 = queue
            .submit(
                key.clone(),
                "p".to_string(),
                "u".to_string(),
                "a".to_string(),
                "local".to_string(),
            )
            .await
            .unwrap();
        let job2 = queue
            .submit(
                key,
                "p".to_string(),
                "u".to_string(),
                "b".to_string(),
                "local".to_string(),
            )
            .await
            .unwrap();
        assert_eq!(job1.id, job2.id);
        assert_eq!(job1.prompt, "a");
    }

    #[tokio::test]
    async fn capacity_backpressure() {
        let dir = TempDir::new().unwrap();
        let mut cfg = test_config(&dir);
        cfg.max_concurrent_jobs = 1;
        cfg.max_queue_size = 4;
        let queue = JobQueue::open(&cfg).unwrap();

        for i in 0..4 {
            let result = queue
                .submit(
                    IdempotencyKey::new(),
                    "p".to_string(),
                    "u".to_string(),
                    format!("prompt-{i}"),
                    "local".to_string(),
                )
                .await;
            if i < 4 {
                assert!(result.is_ok());
            }
        }
        let err = queue
            .submit(
                IdempotencyKey::new(),
                "p".to_string(),
                "u".to_string(),
                "overflow".to_string(),
                "local".to_string(),
            )
            .await;
        assert!(matches!(err, Err(QueueError::Backpressure { .. })));
    }

    #[tokio::test]
    async fn idempotent_retry_is_not_rejected_by_full_capacity() {
        let dir = TempDir::new().unwrap();
        let mut cfg = test_config(&dir);
        cfg.max_queue_size = 1;
        let queue = JobQueue::open(&cfg).unwrap();
        let key = IdempotencyKey::new();
        let first = queue
            .submit(
                key.clone(),
                "p".to_string(),
                "u".to_string(),
                "first".to_string(),
                "local".to_string(),
            )
            .await
            .unwrap();
        let retry = queue
            .submit(
                key,
                "p".to_string(),
                "u".to_string(),
                "different payload must be ignored".to_string(),
                "local".to_string(),
            )
            .await
            .unwrap();
        assert_eq!(first.id, retry.id);
        assert_eq!(retry.prompt, "first");
    }
}
