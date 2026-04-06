// ToolActor is currently implemented as an inline tokio::spawn in SessionActor.
// This module is reserved for the Phase 3 sandbox integration where
// ToolActor becomes a separate supervised actor with SandboxStack applied
// before execve. For now it re-exports the session module types.
pub use crate::actors::session::{ExecuteToolMsg, ExecuteToolReply};
