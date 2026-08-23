// NOT-YET-WIRED SUBSYSTEM.
//
// The items below are implemented but not reachable from any entry point: the
// gRPC service that will consume them still returns `UNIMPLEMENTED` (F02), and
// the Python adapter has not been migrated onto it (F01). The allowance is
// scoped to this module and names the reason, so the unreachability stays
// visible and auditable. Do NOT widen it to the crate, and do NOT add
// `unused_imports`/`unused_variables` here — a blanket crate-level allow is
// exactly what previously concealed three unreachable security controls.
#![allow(dead_code)]

// ToolActor is currently implemented as an inline tokio::spawn in SessionActor.
// This module is reserved for the Phase 3 sandbox integration where
// ToolActor becomes a separate supervised actor with SandboxStack applied
// before execve. For now it re-exports the session module types.
