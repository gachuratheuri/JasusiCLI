use std::path::PathBuf;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use jasusi_service::{
    DeploymentProfile, DrainManager, HealthRegistry, JobQueue, JobState, MetricsRecorder,
    QuotaManager, QuotaRequest, ServiceConfig,
};
use tokio::sync::{mpsc, Mutex};
use tokio_stream::wrappers::ReceiverStream;
use tonic::{Request, Response, Status};

use super::proto::jasus_core_service_server::{JasusCoreService, JasusCoreServiceServer};
use super::proto::{
    control_service_server::{ControlService, ControlServiceServer},
    DeploymentProfile as ProtoDeploymentProfile, Empty, HealthStatus, JobEvent as ProtoJobEvent,
    JobReference, JobStatus, JobSubmit, LedgerEntry, LedgerQuery, LedgerStatus, MemoryEntry,
    MemoryQuery, MemoryResponse, MetricsSnapshot, QuotaCheck, QuotaStatus, ReadinessStatus,
    RollbackRequest, RollbackResult, SessionKey, SessionState, SessionUpdate, ToolEvent,
    ToolRequest, UpsertResult, VersionInfo,
};

pub struct SocketGuard {
    path: PathBuf,
}

impl SocketGuard {
    pub fn new(path: PathBuf) -> Self {
        Self { path }
    }
}

impl Drop for SocketGuard {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
    }
}

pub fn socket_path() -> PathBuf {
    PathBuf::from(format!("/tmp/jasusi-{}.sock", std::process::id()))
}

type GrpcResult<T> = Result<Response<T>, Status>;
type StreamPin<T> = Pin<Box<dyn tokio_stream::Stream<Item = Result<T, Status>> + Send>>;

pub struct JasusCoreServiceImpl;

#[tonic::async_trait]
impl JasusCoreService for JasusCoreServiceImpl {
    type ExecuteToolStream = StreamPin<ToolEvent>;

    async fn execute_tool(
        &self,
        _request: Request<ToolRequest>,
    ) -> GrpcResult<Self::ExecuteToolStream> {
        Err(Status::unimplemented(
            "execute_tool is not implemented in RPC service yet",
        ))
    }

    async fn upsert_memory(&self, _request: Request<MemoryEntry>) -> GrpcResult<UpsertResult> {
        Err(Status::unimplemented(
            "upsert_memory is not implemented in RPC service yet",
        ))
    }

    async fn query_memory(&self, _request: Request<MemoryQuery>) -> GrpcResult<MemoryResponse> {
        Err(Status::unimplemented(
            "query_memory is not implemented in RPC service yet",
        ))
    }

    async fn rollback_memory(
        &self,
        _request: Request<RollbackRequest>,
    ) -> GrpcResult<RollbackResult> {
        Err(Status::unimplemented(
            "rollback_memory is not implemented in RPC service yet",
        ))
    }

    async fn verify_ledger(&self, _request: Request<Empty>) -> GrpcResult<LedgerStatus> {
        Err(Status::unimplemented(
            "verify_ledger is not implemented in RPC service yet",
        ))
    }

    type GetLedgerEntriesStream = StreamPin<LedgerEntry>;

    async fn get_ledger_entries(
        &self,
        _request: Request<LedgerQuery>,
    ) -> GrpcResult<Self::GetLedgerEntriesStream> {
        Err(Status::unimplemented(
            "get_ledger_entries is not implemented in RPC service yet",
        ))
    }

    async fn get_session_state(&self, _request: Request<SessionKey>) -> GrpcResult<SessionState> {
        Err(Status::unimplemented(
            "get_session_state is not implemented in RPC service yet",
        ))
    }

    async fn update_session(&self, _request: Request<SessionUpdate>) -> GrpcResult<Empty> {
        Err(Status::unimplemented(
            "update_session is not implemented in RPC service yet",
        ))
    }
}

#[derive(Debug)]
struct QuotaReservation {
    user_id: String,
    project_id: String,
    input_tokens: u64,
}

/// Control-plane implementation for reliability and operations.
///
/// The service owns no model/tool execution policy. It admits bounded durable
/// jobs and exposes health, telemetry, quota, and lifecycle state from the
/// same primitives used by the worker process.
pub struct ControlServiceImpl {
    config: ServiceConfig,
    health: HealthRegistry,
    metrics: MetricsRecorder,
    quota: QuotaManager,
    drain: DrainManager,
    queue: Arc<JobQueue>,
    reservations: Arc<Mutex<std::collections::HashMap<String, QuotaReservation>>>,
}

impl ControlServiceImpl {
    pub fn new(config: ServiceConfig) -> Result<Self, Box<dyn std::error::Error>> {
        config.validate().map_err(std::io::Error::other)?;
        let queue = Arc::new(JobQueue::open(&config)?);
        let health = HealthRegistry::new();
        health.register(
            "configuration",
            jasusi_service::Probe::Ready {
                reason: "configuration validated".to_string(),
            },
        );
        health.register(
            "queue",
            jasusi_service::Probe::Ready {
                reason: "durable queue opened".to_string(),
            },
        );
        Ok(Self {
            config,
            health,
            metrics: MetricsRecorder::new(),
            quota: QuotaManager::service_defaults(),
            drain: DrainManager::new(),
            queue,
            reservations: Arc::new(Mutex::new(std::collections::HashMap::new())),
        })
    }

    async fn release_reservation_if_terminal(&self, id: &str) {
        let Some(job) = self.queue.get(id).await.ok().flatten() else {
            return;
        };
        if matches!(
            job.state,
            JobState::Completed | JobState::Failed | JobState::Cancelled
        ) {
            if let Some(reservation) = self.reservations.lock().await.remove(id) {
                self.quota.release(&QuotaRequest {
                    user_id: Some(&reservation.user_id),
                    project_id: Some(&reservation.project_id),
                    input_tokens: reservation.input_tokens,
                    ..QuotaRequest::default()
                });
            }
        }
    }
}

#[tonic::async_trait]
impl ControlService for ControlServiceImpl {
    async fn get_version(&self, _request: tonic::Request<Empty>) -> GrpcResult<VersionInfo> {
        Ok(Response::new(VersionInfo {
            version: env!("CARGO_PKG_VERSION").to_string(),
            profile: self.config.profile.to_string(),
            git_sha: option_env!("GIT_SHA").unwrap_or_default().to_string(),
        }))
    }

    async fn health(&self, _request: tonic::Request<Empty>) -> GrpcResult<HealthStatus> {
        Ok(Response::new(HealthStatus {
            healthy: self.config.validate().is_ok(),
            version: env!("CARGO_PKG_VERSION").to_string(),
            profile: match self.config.profile {
                DeploymentProfile::Local => ProtoDeploymentProfile::Local as i32,
                DeploymentProfile::Service => ProtoDeploymentProfile::Service as i32,
            },
        }))
    }

    async fn readiness(&self, _request: tonic::Request<Empty>) -> GrpcResult<ReadinessStatus> {
        let (ready, components) = self.health.readiness_status();
        Ok(Response::new(ReadinessStatus {
            ready,
            components: components
                .into_iter()
                .map(|component| super::proto::ComponentStatus {
                    name: component.name,
                    ready: component.ready,
                    reason: component.reason,
                })
                .collect(),
        }))
    }

    async fn get_metrics(&self, _request: tonic::Request<Empty>) -> GrpcResult<MetricsSnapshot> {
        let metrics = self
            .metrics
            .families()
            .into_iter()
            .flat_map(|family| family.points)
            .map(|point| super::proto::MetricPoint {
                name: point.name,
                value: point.value,
                unit: point.unit,
                timestamp_ms: point.timestamp_ms,
                labels: point.labels.into_iter().collect(),
            })
            .collect();
        Ok(Response::new(MetricsSnapshot { metrics }))
    }

    async fn check_quota(&self, request: tonic::Request<QuotaCheck>) -> GrpcResult<QuotaStatus> {
        let request = request.into_inner();
        let verdict = self.quota.check(&QuotaRequest {
            user_id: (!request.user_id.is_empty()).then_some(request.user_id.as_str()),
            project_id: (!request.project_id.is_empty()).then_some(request.project_id.as_str()),
            provider: (!request.provider.is_empty()).then_some(request.provider.as_str()),
            tool: (!request.tool.is_empty()).then_some(request.tool.as_str()),
            input_tokens: request.requested,
            ..QuotaRequest::default()
        });
        Ok(Response::new(QuotaStatus {
            allowed: verdict.allowed,
            remaining: verdict.remaining,
            limit: verdict.limit,
            scope: verdict.scope.to_string(),
            reason: verdict.reason,
        }))
    }

    async fn submit_job(&self, request: tonic::Request<JobSubmit>) -> GrpcResult<JobReference> {
        let request = request.into_inner();
        let key = jasusi_service::IdempotencyKey::parse(&request.idempotency_key)
            .map_err(Status::invalid_argument)?;
        let user_id = if request.user_id.is_empty() {
            "anonymous".to_string()
        } else {
            request.user_id
        };
        let project_id = if request.project_id.is_empty() {
            "default".to_string()
        } else {
            request.project_id
        };
        let key = jasusi_service::IdempotencyKey::scoped(&[&user_id, &project_id], &key);
        let requested_profile = if request.requested_profile.is_empty() {
            self.config.profile.to_string()
        } else {
            request.requested_profile
        };
        let key = jasusi_service::IdempotencyKey::scoped(&[&user_id, &project_id], &key);
        if let Some(existing) = self
            .queue
            .find_by_idempotency(&key)
            .await
            .map_err(|error| Status::internal(error.to_string()))?
        {
            return Ok(Response::new(JobReference {
                job_id: existing.id.to_string(),
            }));
        }
        let input_tokens = u64::try_from(request.prompt.len().div_ceil(4)).unwrap_or(u64::MAX);
        let quota = self.quota.try_admit(&QuotaRequest {
            user_id: Some(&user_id),
            project_id: Some(&project_id),
            input_tokens,
            ..QuotaRequest::default()
        });
        if !quota.allowed {
            self.metrics.record_rejection("quota");
            return Err(Status::resource_exhausted(quota.reason));
        }
        let job = match self
            .queue
            .submit(
                key,
                project_id.clone(),
                user_id.clone(),
                request.prompt,
                requested_profile,
            )
            .await
        {
            Ok(job) => job,
            Err(error) => {
                self.quota.release(&QuotaRequest {
                    user_id: Some(&user_id),
                    project_id: Some(&project_id),
                    input_tokens,
                    ..QuotaRequest::default()
                });
                return Err(Status::resource_exhausted(error.to_string()));
            }
        };
        self.reservations.lock().await.insert(
            job.id.to_string(),
            QuotaReservation {
                user_id,
                project_id,
                input_tokens,
            },
        );
        self.metrics
            .set_queue_depth(self.queue.active_count().await.unwrap_or_default() as u64);
        Ok(Response::new(JobReference {
            job_id: job.id.to_string(),
        }))
    }

    async fn get_job_status(&self, request: tonic::Request<JobReference>) -> GrpcResult<JobStatus> {
        let id = request.into_inner().job_id;
        let job = self
            .queue
            .get(&id)
            .await
            .map_err(|error| Status::internal(error.to_string()))?
            .ok_or_else(|| Status::not_found("job not found"))?;
        self.release_reservation_if_terminal(&id).await;
        Ok(Response::new(job_status(job)))
    }

    async fn cancel_job(&self, request: tonic::Request<JobReference>) -> GrpcResult<JobStatus> {
        let id = request.into_inner().job_id;
        let job = self
            .queue
            .cancel(&id)
            .await
            .map_err(|error| Status::failed_precondition(error.to_string()))?;
        self.release_reservation_if_terminal(&id).await;
        self.metrics.record_cancellation(0, 0);
        Ok(Response::new(job_status(job)))
    }

    type StreamJobEventsStream = StreamPin<ProtoJobEvent>;

    async fn stream_job_events(
        &self,
        request: tonic::Request<JobReference>,
    ) -> GrpcResult<Self::StreamJobEventsStream> {
        let id = request.into_inner().job_id;
        let mut events = self
            .queue
            .subscribe(&id, true)
            .await
            .map_err(|error| Status::not_found(error.to_string()))?;
        let (tx, rx) = mpsc::channel(64);
        tokio::spawn(async move {
            while let Ok(event) = events.recv().await {
                let message = ProtoJobEvent {
                    job_id: event.job_id,
                    event_type: event.event_type,
                    payload_json: event.payload.to_string(),
                    emitted_at_ms: event.emitted_at_ms,
                };
                if tx.send(Ok(message)).await.is_err() {
                    break;
                }
            }
        });
        Ok(Response::new(Box::pin(ReceiverStream::new(rx))))
    }

    async fn drain(
        &self,
        request: tonic::Request<super::proto::DrainRequest>,
    ) -> GrpcResult<super::proto::DrainStatus> {
        let request = request.into_inner();
        if request.enable {
            self.queue.set_drain(true);
            let timeout = Duration::from_secs(u64::from(request.timeout_seconds).max(1));
            let _ = self.drain.start(timeout).await;
        }
        let active_jobs =
            u32::try_from(self.queue.active_count().await.unwrap_or_default()).unwrap_or(u32::MAX);
        let complete = self.drain.should_shutdown().await || active_jobs == 0;
        if complete {
            self.drain.complete().await;
        }
        Ok(Response::new(super::proto::DrainStatus {
            draining: self.drain.is_draining().await,
            active_jobs,
            complete,
        }))
    }
}

fn job_status(job: jasusi_service::Job) -> JobStatus {
    JobStatus {
        job_id: job.id.to_string(),
        state: job.state.to_string(),
        created_at_ms: job.created_at_ms,
        started_at_ms: job.started_at_ms.unwrap_or_default(),
        completed_at_ms: job.completed_at_ms.unwrap_or_default(),
        terminal_message: job.terminal_message.unwrap_or_default(),
    }
}

#[cfg(unix)]
pub async fn start_server() -> Result<(), Box<dyn std::error::Error>> {
    use tokio::net::UnixListener;
    use tokio_stream::wrappers::UnixListenerStream;

    let config = ServiceConfig::from_env();
    let drain_timeout = Duration::from_secs(config.graceful_drain_timeout_seconds);
    let control = ControlServiceImpl::new(config)?;
    let shutdown_drain = control.drain.clone();
    let shutdown_queue = control.queue.clone();

    let path = socket_path();
    let _guard = SocketGuard::new(path.clone());

    if path.exists() {
        std::fs::remove_file(&path)?;
    }

    let uds = UnixListener::bind(&path)?;

    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::Permissions::from_mode(0o700);
        std::fs::set_permissions(&path, perms)?;
    }

    tracing::info!(socket = %path.display(), pid = std::process::id(), "jasusi-core gRPC server starting");

    let uds_stream = UnixListenerStream::new(uds);
    tonic::transport::Server::builder()
        .add_service(JasusCoreServiceServer::new(JasusCoreServiceImpl))
        .add_service(ControlServiceServer::new(control))
        .serve_with_incoming_shutdown(uds_stream, async move {
            let _ = tokio::signal::ctrl_c().await;
            shutdown_queue.set_drain(true);
            let _ = shutdown_drain.start(drain_timeout).await;
            loop {
                if shutdown_queue.active_count().await.unwrap_or_default() == 0
                    || shutdown_drain.should_shutdown().await
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
            shutdown_drain.complete().await;
        })
        .await?;

    Ok(())
}

#[cfg(not(unix))]
pub async fn start_server() -> Result<(), Box<dyn std::error::Error>> {
    let config = ServiceConfig::from_env();
    let drain_timeout = Duration::from_secs(config.graceful_drain_timeout_seconds);
    let control = ControlServiceImpl::new(config)?;
    let shutdown_drain = control.drain.clone();
    let shutdown_queue = control.queue.clone();
    let addr = "127.0.0.1:50051".parse()?;

    tracing::info!(%addr, pid = std::process::id(), "jasusi-core gRPC server starting (TCP fallback, non-Unix)");

    tonic::transport::Server::builder()
        .add_service(JasusCoreServiceServer::new(JasusCoreServiceImpl))
        .add_service(ControlServiceServer::new(control))
        .serve_with_shutdown(addr, async move {
            let _ = tokio::signal::ctrl_c().await;
            shutdown_queue.set_drain(true);
            let _ = shutdown_drain.start(drain_timeout).await;
            loop {
                if shutdown_queue.active_count().await.unwrap_or_default() == 0
                    || shutdown_drain.should_shutdown().await
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(50)).await;
            }
            shutdown_drain.complete().await;
        })
        .await?;

    Ok(())
}
