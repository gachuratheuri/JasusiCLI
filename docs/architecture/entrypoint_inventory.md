# Executable Entry Point Inventory and Disposition Classification

* **Status:** Complete (Phase 0 Governance Milestone G0)
* **Date:** 2026-07-24
* **Scope:** Full Repository Entry Point Audit

---

## 1. Classification Definitions

* **Canonical:** The authoritative, target implementation that will be maintained, hardened, and exposed for production use.
* **Transitional:** Active component retained temporarily during Phase 1–6 migration under strict containment, scheduled for refactoring or conversion.
* **Test-Only:** Entry points exclusively used by automated test harnesses, benchmark suites, or mock servers.
* **Obsolete:** Superseded or dangerous entry points marked for complete removal/deprecation.

---

## 2. Inventory and Classification Matrix

| Entry Point Location | Type / Interface | Current Behavior | Classification | Target Disposition (Phase 1–8) |
|---|---|---|---|---|
| `rust/crates/jasusi-core/src/main.rs` | Rust CLI Binary | Bypasses CLI arguments; automatically starts gRPC server on any invocation. | **Transitional** | Refactor in Phase 1.1: `main()` dispatches CLI commands directly; add explicit `serve` / `daemon` subcommand for gRPC. |
| `rust/crates/jasusi-core/src/rpc/server.rs` | gRPC Service (`JasusCoreService`) | Returns hardcoded stub success (`verified: true`, `success: true`) for memory/ledger/tool calls. | **Transitional** | Harden in Phase 1.1 & Phase 2: replace stubs with `grpc::Status::unimplemented()` until real handlers exist. |
| `jasusi_cli/cli/entry.py` | Python CLI Entrypoint (`jasusi`) | Dispatches CLI commands via legacy Python orchestrator. | **Transitional** | Retain as CLI wrapper during Phase 1–2; delegate execution to Rust RPC/engine in Phase 2. |
| `app.py` | Python FastAPI Web Server | Runs FastAPI app; invokes string-interpolated `python -c` script subprocesses for tasks and fixes. | **Transitional** | Enforce local-only containment in Phase 0/1; replace `python -c` with typed gRPC IPC client in Phase 1.2 & Phase 2. |
| `start_web.ps1` | PowerShell Web Launcher | Launches `uvicorn app:app` and started Cloudflare Tunnel (`cloudflared`). | **Transitional** | Hardened in Phase 0: disabled public tunneling, forced 127.0.0.1 binding only. Re-evaluate post-G3. |
| `rust/crates/jasusi-core/build.rs` | Build Script | Compiles `proto/jasusi.proto` via `tonic_build`. | **Canonical** | Retain as core build infrastructure for gRPC interface compilation. |
| `jasusi_cli/core/orchestrator.py` | Python Legacy Orchestrator | Executes LLM turns, tool invocation loops, and fallback routing in Python. | **Transitional** | Freeze in Phase 2; migrate all orchestration semantics to Rust `SessionActor` and retire. |
| `jasusi_cli/routing/scored_router.py` | Python Heuristic Classifier | 6-role prompt classifier used by web interface. | **Transitional** | Consolidate with Rust classifier (`crates/runtime/src/router.rs`) into single decision contract in Phase 2. |
| `jasusi_cli/core/router.py` | Python Legacy Router | Alternative keyword router used by legacy orchestrator. | **Obsolete** | Deprecate and remove in Phase 2 in favor of single canonical Rust router. |
| `jasusi_cli/tools/implementations/bash_tool.py` | Python Subprocess Tool | Executes arbitrary shell commands with insufficient process tree termination. | **Transitional** | Replace with Rust `SupervisorActor` and OS sandboxing in Phase 3. |
| `rust/crates/runtime/tests/integration_tests.rs` | Rust Integration Test Suite | Exercises runtime integration and session state. | **Test-Only** | Maintain and expand with cross-platform negative security test suites. |
| `jasusi_cli/tests/test_phase7.py` - `test_phase9.py` | Python Pytest Suite | Exercises Python unit/integration features. | **Test-Only** | Maintain for Python adapter validation; align with contract tests. |
| `src/` (Legacy Porting Files) | Historical Source Port | Standalone source files from earlier iterations. | **Obsolete** | Reconcile or retire during Phase 7 code cleanup. |

---

## 3. Entry Point Security Containment Policies (Phase 0)

1. **No External Network Binding:** `app.py` and `start_web.ps1` MUST NOT bind to `0.0.0.0` or instantiate external tunnels until Gate G3 security review passes.
2. **Subprocess Invocation Guard:** Direct `python -c` string interpolation in `app.py` is flagged as F10/High-Risk Injection and scheduled for complete elimination in Phase 1.2.
3. **RPC Status Truthfulness:** RPC entry points MUST NOT report fake success for unbacked operations (F02).
