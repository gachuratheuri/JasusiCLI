# Security Governance, Traceability Matrix, and Security Policy

* **Status:** Complete (Phase 0 Governance Milestone G0)
* **Date:** 2026-07-24
* **Scope:** Findings F01–F16 Traceability, Waiver Governance, and Platform Security Guarantees

---

## 1. Governance Roles

To maintain strict dual-control accountability, every finding and phase deliverable is assigned:
* **Accountable Owner:** Lead engineer responsible for designing, implementing, and verifying the remediation.
* **Independent Reviewer:** Senior engineer or security researcher responsible for auditing code changes, reviewing evidence, and approving closure.

---

## 1a. Closure evidence standard (mandatory)

A finding may not be marked closed on the strength of a passing test alone. The
first remediation pass produced three security controls that were implemented,
exported, unit-tested, and **never called by any production code path**
(`validate_execution_allowed`, `validate_and_apply_patch`, and the web
`canonicalize_path`). Every gate signed off green while the system remained
unprotected.

Closure therefore requires **all** of:

1. **Reachability.** The control is invoked on the path a real request takes.
2. **Removal sensitivity.** Deleting or bypassing the control makes a test fail.
   A test that exercises the function directly does not satisfy this.
3. **Platform truth.** Where a guarantee is unavailable on a supported platform,
   the code fails closed and the documentation states the limitation. Silence is
   not an acceptable substitute for an unimplemented control.
4. **No suppression.** `dead_code`, `unused_imports`, and `unused_variables` must
   not be suppressed in crates carrying security logic — blanket suppression is
   what concealed the unreachable controls above.

## 2. Findings Traceability Matrix (F01–F16)

> **Status column reflects verified behaviour, not intended behaviour.** Entries
> marked OPEN were previously recorded as closed but did not survive
> verification against the code.

| Finding ID | Finding Description | Severity | Accountable Owner | Independent Reviewer | Target Remediation Phase | Required Closure Evidence | Verification Test |
|---|---|---|---|---|---|---|---|
| **F01** | Dual Python/Rust authority ("split-brain runtime") | **P0** | Lead Architect | Security Lead | Phase 0–2 | ADR-001 accepted; single engine gRPC architecture contract established; Python tool/memory execution disabled. | Contract integration tests proving CLI and Web use identical Rust core engine. |
| **F02** | Rust CLI argument bypass and fake gRPC stub success | **P0** | Systems Engineer | Lead Architect | Phase 1.1 | CLI `main()` dispatches commands correctly; gRPC stubs replaced with `UNIMPLEMENTED` status or real ledger persistence. | CLI argument dispatch test suite; gRPC protocol status verification suite. |
| **F03** | Python provider interface mismatch in `MultiProviderClient` | **P1** | Core Developer | Systems Engineer | Phase 4 | Single provider trait defined; client factory populates only validated providers; complete factory/provider integration. | Provider end-to-end integration test suite with deterministic mocks. |
| **F04** | Permission default and ordering flaw (`Prompt` allows silently) | **P0** | Security Lead | Systems Engineer | Phase 1.3 & Phase 3 | Explicit capability-based decision table replacing enum comparison; `Prompt` mode always prompts or denies in non-interactive environments. | Property-based test suite proving `Prompt` can never evaluate to silent Allow. |
| **F05** | Declarative non-enforcing sandbox claims | **P0** | Systems Engineer | Security Lead | Phase 3 | OS-level isolation (Landlock/cgroups/Restricted Tokens) enforced in child process before execution; fail closed if unavailable. | Platform-specific negative security tests demonstrating denied filesystem/network ops. |
| **F06** | Unsafe `run_fix` source code overwrite | **P1** | Core Developer | Security Lead | Phase 1.3 & Phase 5 | Direct file overwrite disabled; structured patch contracts with syntax validation, diff review, and rollback history implemented. | Validation failure test proving invalid patch leaves original file unchanged. |
| **F07** | Non-conformant buffered SSE implementation | **P1** | Web Lead | Systems Engineer | Phase 4 | Truly incremental byte-buffer SSE parser supporting multiline data, CRLF, split UTF-8, and early token streaming. | Incremental buffer parser test suite with split UTF-8 and fragmented chunks. |
| **F08** | Process timeout leaves orphan child processes | **P0** | Systems Engineer | Security Lead | Phase 1.1 & Phase 4 | Full child process group / job object creation with signal propagation; process tree termination on timeout or disconnect. | Process tree leak test proving zero orphan processes survive cancellation. |
| **F09** | Disconnected sessions, budgets, remote mode, and compaction | **P1** | Core Developer | Lead Architect | Phase 4 | Unified session lifecycle with token budget enforcement, reachability-based compaction, and exact session resume. | Session resume and crash recovery test suite verifying state reproduction. |
| **F10** | Web authentication, CORS, path handling, and resource weaknesses | **P0** | Web Lead | Security Lead | Phase 1.2 | Local-only CORS allowlist; elimination of `python -c` interpolation; canonical path traversal defense; file upload size bounds. | Web security test suite covering path traversal, CORS, and upload exhaustion. |
| **F11** | Router and model roster divergence | **P1** | ML Architect | Lead Architect | Phase 2 | Canonical prompt router and model registry established; identical routing decisions across CLI and Web entry points. | Labelled routing evaluation corpus measuring macro-F1 and route equivalence. |
| **F12** | Misleading tests and broken CI targets | **P1** | QA Lead | Systems Engineer | Phase 7 | Full Rust workspace CI, Python verification, and repair of Windows target compilation errors (`mcp_stdio.rs`). | Clean multi-platform CI build green across Linux, macOS, and Windows lanes. |
| **F13** | Monolithic modules and duplicate implementations | **P1** | Lead Architect | Core Developer | Phase 7 | Decomposed 255KB tools module; eliminated duplicate bash/file/router implementations across Python and Rust. | Architecture boundary checks and dead code elimination audit. |
| **F14** | Package version, documentation, and license drift | **P2** | Release Lead | QA Lead | Phase 7 | Single version source of truth; updated README; MIT LICENSE file committed; clean package installation test. | Clean environment package install/exercise/uninstall smoke test. |
| **F15** | Exposed credentials, tracked transcripts, and binary blobs | **P0** | Security Lead | Release Lead | Phase 1.3 & Phase 7 | Tracked session transcripts and binaries removed from repo; automated secret scanning active; exposed keys rotated. | Secret scanner scan report verifying zero plain-text secrets in repository. |
| **F16** | Divergent memory implementations and unverified ledger claims | **P1** | Systems Engineer | Lead Architect | Phase 6 | Unified SQLite store; transactional schema migrations; SHA-256 hash-chain verification with tamper detection. | Cryptographic ledger tamper detection test suite verifying sequence validation. |

---

## 3. Security Waiver Governance Process

If an operational constraint prevents full technical remediation of a finding within its target phase, a **Formal Security Waiver** MUST be executed. A waiver requires ALL of the following mandatory elements:

1. **Waiver ID:** Unique identifier (`WVR-YY-XXX`).
2. **Associated Finding:** Link to target finding ID (F01–F16).
3. **Accountable Owner Sign-off:** Named approval from the Accountable Owner.
4. **Independent Reviewer Sign-off:** Named approval from the Independent Reviewer.
5. **Technical Rationale:** Detailed engineering explanation of why full remediation cannot be immediately applied.
6. **Expiration Date:** Maximum valid duration not exceeding 30 calendar days.
7. **Compensating Security Control:** Mandatory secondary control mitigating the risk during the waiver window (e.g. strict local loopback binding mitigating lack of network sandboxing).
8. **Tracked Follow-up Issue:** Link to tracked repository issue scheduled for resolution prior to waiver expiration.

---

## 4. Supported Operating Systems & Security Guarantees

### Supported Operating Systems

* **Linux (Ubuntu 22.04 LTS / Debian 12+, x86_64, aarch64):** Full feature tier. Supports Landlock LSM, cgroups v2, user/mount/network namespaces, and eBPF monitoring.
* **macOS (13+ Ventura / Sonoma, Apple Silicon & Intel):** Standard feature tier. Supports sandbox-exec / POSIX process isolation and seatbelt profiles.
* **Windows (Windows 11 / Windows Server 2022, x86_64):** Supported execution platform under explicit security constraints (see below).

### Explicitly Unsupported Security Guarantees

1. **Uncontainerized Windows Process Sandboxing:** On Windows OS without hypervisor isolation (Hyper-V/Docker) or AppContainer tokens, process isolation CANNOT guarantee absolute defense against kernel-level object access or symlink manipulation. Users on Windows MUST NOT run untrusted tool commands without containerization.
2. **Unprivileged Symlink Protection on Windows:** Windows Developer Mode / unprivileged symlink behavior does not provide TOCTOU protection equivalent to Linux Landlock mount namespaces.
3. **Remote Exposure Without TLS & gRPC Authentication:** Running the web interface or gRPC daemon across external network boundaries (`0.0.0.0`) without TLS termination and strong mutual authentication is EXPLICITLY UNSUPPORTED and prohibited until Phase 3 sign-off.
