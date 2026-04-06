use std::collections::HashMap;

use kameo::actor::{ActorRef, WeakActorRef};
use kameo::error::{ActorStopReason, Infallible};
use kameo::mailbox::unbounded::UnboundedMailbox;
use kameo::message::Context;
use kameo::Actor;

use crate::actors::session::SessionActor;

pub struct DaemonSupervisor {
    sessions: HashMap<String, ActorRef<SessionActor>>,
}

impl Actor for DaemonSupervisor {
    type Mailbox = UnboundedMailbox<Self>;
    type Error = Infallible;
}

impl DaemonSupervisor {
    pub fn new() -> Self {
        Self {
            sessions: HashMap::new(),
        }
    }
}

pub struct SpawnSession {
    pub session_id: String,
    pub project: String,
}

pub struct SpawnSessionReply {
    pub actor_ref: ActorRef<SessionActor>,
    pub was_existing: bool,
}

impl kameo::message::Message<SpawnSession> for DaemonSupervisor {
    type Reply = Result<SpawnSessionReply, String>;

    async fn handle(
        &mut self,
        msg: SpawnSession,
        _ctx: &mut Context<Self, Self::Reply>,
    ) -> Self::Reply {
        if let Some(existing) = self.sessions.get(&msg.session_id) {
            return Ok(SpawnSessionReply {
                actor_ref: existing.clone(),
                was_existing: true,
            });
        }
        let actor = SessionActor::new(msg.session_id.clone(), msg.project.clone());
        let actor_ref = kameo::spawn(actor);
        self.sessions
            .insert(msg.session_id.clone(), actor_ref.clone());
        Ok(SpawnSessionReply {
            actor_ref,
            was_existing: false,
        })
    }
}

pub struct GetSession {
    pub session_id: String,
}

impl kameo::message::Message<GetSession> for DaemonSupervisor {
    type Reply = Option<ActorRef<SessionActor>>;

    async fn handle(
        &mut self,
        msg: GetSession,
        _ctx: &mut Context<Self, Self::Reply>,
    ) -> Self::Reply {
        self.sessions.get(&msg.session_id).cloned()
    }
}

pub struct ListSessions;

impl kameo::message::Message<ListSessions> for DaemonSupervisor {
    type Reply = Vec<(String, String)>;

    async fn handle(
        &mut self,
        _msg: ListSessions,
        _ctx: &mut Context<Self, Self::Reply>,
    ) -> Self::Reply {
        self.sessions
            .keys()
            .map(|k| (k.clone(), "active".to_string()))
            .collect()
    }
}

pub struct RemoveSession {
    pub session_id: String,
}

impl kameo::message::Message<RemoveSession> for DaemonSupervisor {
    type Reply = bool;

    async fn handle(
        &mut self,
        msg: RemoveSession,
        _ctx: &mut Context<Self, Self::Reply>,
    ) -> Self::Reply {
        self.sessions.remove(&msg.session_id).is_some()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use kameo::spawn;

    #[tokio::test]
    async fn test_spawn_creates_new_session() {
        let supervisor = spawn(DaemonSupervisor::new());
        let reply = supervisor
            .ask(SpawnSession {
                session_id: "test-001".to_string(),
                project: "test-project".to_string(),
            })
            .await
            .unwrap();
        assert!(!reply.was_existing);
    }

    #[tokio::test]
    async fn test_spawn_returns_existing_session() {
        let supervisor = spawn(DaemonSupervisor::new());
        let _first = supervisor
            .ask(SpawnSession {
                session_id: "sess-1".to_string(),
                project: "proj".to_string(),
            })
            .await
            .unwrap();
        let second = supervisor
            .ask(SpawnSession {
                session_id: "sess-1".to_string(),
                project: "proj".to_string(),
            })
            .await
            .unwrap();
        assert!(second.was_existing);
    }

    #[tokio::test]
    async fn test_list_sessions() {
        let supervisor = spawn(DaemonSupervisor::new());
        supervisor
            .ask(SpawnSession {
                session_id: "s1".to_string(),
                project: "p1".to_string(),
            })
            .await
            .unwrap();
        supervisor
            .ask(SpawnSession {
                session_id: "s2".to_string(),
                project: "p2".to_string(),
            })
            .await
            .unwrap();
        let list = supervisor.ask(ListSessions).await.unwrap();
        assert_eq!(list.len(), 2);
    }

    #[tokio::test]
    async fn test_remove_session() {
        let supervisor = spawn(DaemonSupervisor::new());
        supervisor
            .ask(SpawnSession {
                session_id: "del-me".to_string(),
                project: "p".to_string(),
            })
            .await
            .unwrap();
        let removed = supervisor
            .ask(RemoveSession {
                session_id: "del-me".to_string(),
            })
            .await
            .unwrap();
        assert!(removed);
        let not_there = supervisor
            .ask(RemoveSession {
                session_id: "del-me".to_string(),
            })
            .await
            .unwrap();
        assert!(!not_there);
    }

    #[tokio::test]
    async fn test_concurrent_sessions_are_isolated() {
        let supervisor = spawn(DaemonSupervisor::new());
        for i in 0..5 {
            supervisor
                .ask(SpawnSession {
                    session_id: format!("concurrent-{i}"),
                    project: "proj".to_string(),
                })
                .await
                .unwrap();
        }
        let removed = supervisor
            .ask(RemoveSession {
                session_id: "concurrent-2".to_string(),
            })
            .await
            .unwrap();
        assert!(removed);
        let list = supervisor.ask(ListSessions).await.unwrap();
        assert_eq!(list.len(), 4);
        assert!(list.iter().all(|(id, _)| id != "concurrent-2"));
    }
}
