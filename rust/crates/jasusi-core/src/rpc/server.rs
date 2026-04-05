use std::path::PathBuf;
use std::pin::Pin;

use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;
use tonic::{Request, Response, Status};

use super::proto::jasus_core_service_server::{JasusCoreService, JasusCoreServiceServer};
use super::proto::{
    Empty, LedgerEntry, LedgerQuery, LedgerStatus, MemoryEntry, MemoryQuery, MemoryResponse,
    RollbackRequest, RollbackResult, SessionKey, SessionState, SessionUpdate, ToolEvent, ToolOutput,
    ToolRequest, UpsertResult,
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

    async fn execute_tool(&self, _request: Request<ToolRequest>) -> GrpcResult<Self::ExecuteToolStream> {
        let (tx, rx) = mpsc::channel(1);
        tokio::spawn(async move {
            let event = ToolEvent {
                event: Some(super::proto::tool_event::Event::Output(ToolOutput {
                    content: "stub".into(),
                    is_error: false,
                })),
            };
            let _ = tx.send(Ok(event)).await;
        });
        let stream = ReceiverStream::new(rx);
        Ok(Response::new(Box::pin(stream)))
    }

    async fn upsert_memory(&self, _request: Request<MemoryEntry>) -> GrpcResult<UpsertResult> {
        Ok(Response::new(UpsertResult { success: true }))
    }

    async fn query_memory(&self, _request: Request<MemoryQuery>) -> GrpcResult<MemoryResponse> {
        Ok(Response::new(MemoryResponse { results: vec![] }))
    }

    async fn rollback_memory(&self, _request: Request<RollbackRequest>) -> GrpcResult<RollbackResult> {
        Ok(Response::new(RollbackResult {
            removed_count: 0,
            ledger_seq: 0,
        }))
    }

    async fn verify_ledger(&self, _request: Request<Empty>) -> GrpcResult<LedgerStatus> {
        Ok(Response::new(LedgerStatus {
            verified: true,
            tampered_at_seq: None,
        }))
    }

    type GetLedgerEntriesStream = StreamPin<LedgerEntry>;

    async fn get_ledger_entries(
        &self,
        _request: Request<LedgerQuery>,
    ) -> GrpcResult<Self::GetLedgerEntriesStream> {
        let (_tx, rx) = mpsc::channel(1);
        let stream = ReceiverStream::new(rx);
        Ok(Response::new(Box::pin(stream)))
    }

    async fn get_session_state(&self, _request: Request<SessionKey>) -> GrpcResult<SessionState> {
        Ok(Response::new(SessionState {
            session_id: String::new(),
            input_tokens: 0,
            output_tokens: 0,
            compaction_count: 0,
            updated_at: String::new(),
        }))
    }

    async fn update_session(&self, _request: Request<SessionUpdate>) -> GrpcResult<Empty> {
        Ok(Response::new(Empty {}))
    }
}

#[cfg(unix)]
pub async fn start_server() -> Result<(), Box<dyn std::error::Error>> {
    use tokio::net::UnixListener;
    use tokio_stream::wrappers::UnixListenerStream;

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
        .serve_with_incoming(uds_stream)
        .await?;

    Ok(())
}

#[cfg(not(unix))]
pub async fn start_server() -> Result<(), Box<dyn std::error::Error>> {
    let addr = "127.0.0.1:50051".parse()?;

    tracing::info!(%addr, pid = std::process::id(), "jasusi-core gRPC server starting (TCP fallback, non-Unix)");

    tonic::transport::Server::builder()
        .add_service(JasusCoreServiceServer::new(JasusCoreServiceImpl))
        .serve(addr)
        .await?;

    Ok(())
}
