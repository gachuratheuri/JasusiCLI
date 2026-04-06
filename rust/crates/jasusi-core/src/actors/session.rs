use kameo::actor::{ActorRef, WeakActorRef};
use kameo::error::{ActorStopReason, Infallible};
use kameo::mailbox::unbounded::UnboundedMailbox;
use kameo::message::Context;
use kameo::Actor;
use std::io::Write;
use tokio::sync::mpsc;

use crate::rpc::proto::{
    tool_event, ProgressUpdate, ToolEvent, ToolOutput,
};

pub struct SessionActor {
    pub session_id: String,
    pub project: String,
    pub turn_count: u32,
}

impl Actor for SessionActor {
    type Mailbox = UnboundedMailbox<Self>;
    type Error = Infallible;

    async fn on_stop(
        &mut self,
        _actor_ref: WeakActorRef<Self>,
        reason: ActorStopReason,
    ) -> Result<(), Self::Error> {
        if let ActorStopReason::Panicked(err) = &reason {
            // RULE 6: use blocking write — async context may be dropped
            tracing::error!(
                session_id = %self.session_id,
                project = %self.project,
                "SessionActor panicked: {}", err
            );
            let audit_dir = dirs::home_dir()
                .unwrap_or_default()
                .join(".jasusi");
            let _ = std::fs::create_dir_all(&audit_dir);
            let panic_log = audit_dir.join("panic.log");
            let entry = format!(
                "{} session={} project={} error={}\n",
                chrono::Utc::now().to_rfc3339(),
                self.session_id,
                self.project,
                err
            );
            if let Ok(mut f) = std::fs::OpenOptions::new()
                .create(true)
                .append(true)
                .open(&panic_log)
            {
                let _ = f.write_all(entry.as_bytes());
            }
        }
        Ok(())
    }
}

impl SessionActor {
    pub fn new(session_id: String, project: String) -> Self {
        Self {
            session_id,
            project,
            turn_count: 0,
        }
    }
}

pub struct ExecuteToolMsg {
    pub tool_name: String,
    pub input_json: bytes::Bytes,
}

#[derive(kameo::Reply)]
pub struct ExecuteToolReply {
    pub rx: mpsc::Receiver<ToolEvent>,
}

impl kameo::message::Message<ExecuteToolMsg> for SessionActor {
    type Reply = ExecuteToolReply;

    async fn handle(
        &mut self,
        msg: ExecuteToolMsg,
        _ctx: &mut Context<Self, Self::Reply>,
    ) -> Self::Reply {
        self.turn_count += 1;
        let (tx, rx) = mpsc::channel(32);
        let tool_name = msg.tool_name.clone();

        tokio::spawn(async move {
            tracing::info!(tool = %tool_name, "ToolActor executing");
            let _ = tx
                .send(ToolEvent {
                    event: Some(tool_event::Event::Progress(ProgressUpdate {
                        message: format!("Executing {tool_name}"),
                        percent_complete: 50,
                    })),
                })
                .await;
            let _ = tx
                .send(ToolEvent {
                    event: Some(tool_event::Event::Output(ToolOutput {
                        content: format!("Tool {tool_name} executed (stub)"),
                        is_error: false,
                    })),
                })
                .await;
        });

        ExecuteToolReply { rx }
    }
}

pub struct GetState;

#[derive(kameo::Reply)]
pub struct SessionStateReply {
    pub session_id: String,
    pub project: String,
    pub turn_count: u32,
}

impl kameo::message::Message<GetState> for SessionActor {
    type Reply = SessionStateReply;

    async fn handle(
        &mut self,
        _msg: GetState,
        _ctx: &mut Context<Self, Self::Reply>,
    ) -> Self::Reply {
        SessionStateReply {
            session_id: self.session_id.clone(),
            project: self.project.clone(),
            turn_count: self.turn_count,
        }
    }
}
