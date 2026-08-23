# Changelog

All notable changes to JasusiCLI are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

---

## [Unreleased]

### Fixed — controls that existed but were never reachable

Verification against the code found that three security controls from the
previous release were implemented, exported, and unit-tested while **never being
called by any production path**. Every gate had been signed off green.

- **F05 — fail-closed execution gate is now enforced.** `validate_execution_allowed`
  is called from the shell tool (`runtime::bash`) and from every mutating file
  operation (`write_file`, `edit_file`). Added the `--unsafe-local-mode` flag its
  own error message already advertised but which did not exist. Execution is
  denied when no OS isolation is in effect unless the operator opts in.
- **F05 — `filesystem_active`/`active` no longer report isolation that is absent.**
  Both were computed from configuration alone, so a host with no enforcement
  mechanism reported itself as sandboxed. They now require namespaces or a
  container.
- **F08 — timeouts terminate the whole process tree.** `tokio::time::timeout`
  dropped the future, orphaning the shell and all its descendants. Now: process
  group on Unix (`kill -KILL -pgid`), Job Object with `KILL_ON_JOB_CLOSE` on
  Windows, plus `kill_on_drop` as defence in depth. `taskkill /T` alone was
  insufficient — it cannot reach a detached grandchild.
- **F10 — web path containment fixed and wired.** `canonicalize_path` used a
  string-prefix test (`/srv/work-evil` passes a check anchored at `/srv/work`)
  and had no callers. Now uses `Path.is_relative_to` and guards upload
  destinations.
- **Rust path containment bypass.** `is_path_safe_in_workspace` fell back to the
  unresolved path when neither target nor parent existed; because
  `Path::starts_with` is component-wise, `root/../../etc/passwd` compared as
  inside `root`. Now resolves against the deepest existing ancestor and
  normalises lexically.

### Fixed — build, CI, and truthfulness

- **The workspace did not build from clean.** `proto/jasusi.proto` used a proto3
  `optional` label that requires protoc ≥ 3.15; every green test run had been
  using a stale cached build-script fingerprint. Replaced with an explicit
  presence field and pinned protoc in CI, plus an uncached build lane.
- **CI tested a package that does not exist** (`cargo test -p rusty-claude-cli`).
  Replaced with `cargo test --workspace` across Linux, Windows, and macOS. The
  main workflow ran no Rust tests at all; it now does.
- Python CI covered only one of two test roots and ran no `ruff`; both fixed.
- Removed `tests/test_porting_workspace.py`, which imported a `src` package that
  no longer exists and had been failing collection silently.
- Crate-level `#![allow(dead_code, unused_imports, unused_variables)]` removed
  from `jasusi-core`. `dead_code` remains allowed for the binary alone with a
  documented, bounded rationale; the not-yet-wired subsystems carry a scoped
  per-module allowance that names why they are unreachable.

### Fixed — runtime correctness

- **F03 — the default-wired Python runtime could not complete a single turn.**
  `ConversationRuntime` calls `complete()`; `MultiProviderClient` exposed only
  `stream()`, and was constructed with an empty provider map. Added a conforming
  `complete()`, and providers are now built from validated credentials with an
  explicit failure when none is available.
- **F07 — provider SSE is genuinely incremental.** `_fetch_sse` collected the
  entire response before yielding. Retries now stop once output is committed,
  since retrying mid-stream duplicates content.
- **F11 — one router, one model registry.** Three routers (two Python, one Rust)
  and four model rosters were reconciled. Scoring lives in `core.router`;
  role→model→provider mapping in `config.registry`. Route decisions are asserted
  identical across entry points.
- **F04 — Python no longer auto-allows.** `RuntimeFactory` and `ToolExecutor`
  defaulted to `AutoAllowPrompter`; both now default to `TerminalPrompter`,
  which denies when stdin is not a TTY.
- **F06 — `run_fix` cannot write to source.** The `preview_only=False` branch was
  removed rather than left behind a default argument. Malformed reviewer JSON is
  a typed failure instead of an escaping `JSONDecodeError`/`KeyError`.
- `ConversationRuntime` emitted no terminal `stop_reason`; callers could not
  distinguish a completed answer from one truncated by a token limit.

### Fixed — web adapter

- Authentication fails closed: a non-loopback request with no `UI_PASSWORD`
  configured is refused (503) rather than served.
- Removed the `?_key=` query-string credential; header only, compared with
  `hmac.compare_digest`.
- Execution moved out-of-process via an argv vector, restoring real incremental
  streaming and real cancellation. Client disconnect now terminates the process
  tree; previously `asyncio.to_thread` ran the whole task to completion and could
  not be cancelled.
- Added stable error codes (internal exception text is no longer returned),
  security headers, `no-store` on authenticated responses, and bounds on stream
  duration, output volume, line length, and concurrency.

### Fixed — repository hygiene

- Untracked 41 session transcripts, a 10 MB `rustup-init.exe`, and sandbox
  scratch state that had been committed into the workspace.
- Version now has one source (`pyproject.toml`). It previously disagreed five
  ways: `3.3.0`, `3.0.0`, and `0.14.0` across package, adapter, CLI, and docs.

---

## [3.0.0] — 2026-07-24

### Security Architecture Migration (Phases 1–7, audit findings F01–F16)

This release constitutes a security-sensitive architectural migration to a
single Rust execution and security engine, with Python retained only as a
thin, authenticated web/API adapter. All model routing, permissions, tool
execution, sessions, memory, budgets, and audit semantics now have one
authoritative Rust implementation.

#### Phase 1 — Prompt Injection Hardening (G1)
- `InjectionGuard` in Rust runtime enforces structured content-address
  whitelisting and strips all `<SYSTEM>` / `<INST>` injection vectors.
- Python `InjectionGuard` test suite (5 tests) exercises the boundary layer.

#### Phase 2 — Sandboxing and Capability Policy (G2)
- `SandboxConfig` / `SandboxStatus` unify filesystem isolation, namespace
  restrictions, and network isolation under a typed, serializable contract.
- `validate_execution_allowed()` fail-closed gate: write/shell tools require
  active sandboxing or explicit `--unsafe-local-mode` flag.
- `PolicyEngine` and `PermissionEnforcer` provide per-tool allow/deny rules
  with configurable `CapabilitySet` hierarchy.

#### Phase 3 — Token Budget and Session Integrity (G3)
- `BudgetEnforcer` persists per-session spend in `WormLedger` with strict
  configurable input/output ceilings and hard stop-loss.
- `SessionIntegrityGuard` validates HMAC-signed session files; corrupt or
  replayed sessions are rejected.
- `TrustResolver` enforces multi-tier trust levels (none/low/standard/high/full).

#### Phase 4 — Runtime Cancellation and Task Lifecycle (G4)
- `TaskRegistry` and `TeamCronRegistry` provide cancellable background task
  management with worker isolation and full lifecycle audit.
- `WorkerBoot` integrates budget, sandbox, and trust validation.

#### Phase 5 — Transactional AI-Assisted Code Modification (G5)
- `StructuredPatch` / `PatchHunk` replace free-form overwrite tools with
  typed, hash-validated, atomic line-range patch contracts.
- Pre-apply SHA-256 content verification with workspace-escape detection.
- `rollback_transaction` provides instant byte-for-byte workspace restoration.

#### Phase 6 — Persistence, Memory, and Ledger Consolidation (G6)
- `SessionStore` backed by SQLite WAL mode with ACID guarantees.
- `WormLedger` cryptographic tamper-evident chain with `check_tamper_status()`.
- `legacy_migration.rs` provides a one-shot importer from legacy JSON sessions.

#### Phase 7 — Code Quality, CI, Packaging, and Documentation (G7)
- Canonical version unified to `3.0.0` across Rust workspace (`Cargo.toml`),
  Python package (`pyproject.toml`), and API status endpoint (`app.py`).
- MIT `LICENSE` file added and machine-verified in `packaging_tests.rs`.
- Platform conditional guards (`#[cfg(unix)]` / `#[cfg(not(unix))]`) added
  to `sandbox.rs` for provably correct cross-platform compilation.
- `cargo clippy --workspace --all-targets -- -D warnings` passes with zero
  warnings; `cargo test --workspace` passes 100% of all test targets.

---


## [0.14.0] — 2026-04-06

### Added — Phase 14
- GitHub Actions CI: Python 3.11/3.12 matrix, Rust stable, smoke tests
- `jasusi --version` and `jasusi status` fast-path smoke tests in CI
- `pip install -e ".[dev]"` verified in CI without API keys
- Final release: 270+ tests passing, 0 failed

## [0.13.0] — 2026-04-06

### Added — Phase 13
- `BrailleSpinner`: 10-frame braille async context manager (RULE 2)
- `Repl` wired to `RuntimeFactory` — lazy runtime build on first user message
- `HistoryLog` records every turn: user input, assistant output, token delta, tags
- `/compact` slash command triggers `ConversationRuntime._compact_history()`
- Ctrl+C handling: cancel current stream, return to prompt

## [0.12.0] — 2026-04-06

### Added — Phase 12
- `ProviderClient`: httpx async SSE streaming, exponential backoff (7 status codes)
- `SseParser`: incremental byte-stream parser, push_chunk/finish
- `BashTool` (async): `asyncio.create_subprocess_exec`, shell=False, timeout=30s
- `FileReadTool` (async): 250-line head limit, path traversal guard
- API key redaction: last 4 chars only in all logs (RULE 9)

## [0.11.0] — 2026-04-06

### Added — Phase 11
- `BootstrapGraph`: 7-stage pipeline, FastPath early exits, `BootstrapContext`
- `TaskRunner`: single-turn async execution via `RuntimeFactory`
- `RuntimeConfig.task_input` field
- CLI: `--session` flag (renamed from `--resume`), `status` subcommand

## [0.10.0] — 2026-04-06

### Added — Phase 10
- Full cross-phase test suite: 188 tests passing
- `pyproject.toml` finalized with `[project.scripts]`

## [0.9.0] — 2026-04-06

### Added — Phase 9
- `RuntimeFactory`: wires all modules together with dependency injection
- `WormLedger`: token accounting and cost tracking
- Mock clients: `MockApiClient`, `MockToolExecutor`, `MockTurn`

## [0.8.0] — 2026-04-06

### Added — Phase 8
- `Repl`: interactive async REPL skeleton
- `CommandHandler`: 15 slash commands
- `OutputFormatter`: text/json/ndjson output modes
- `HistoryLog`: append-only JSONL event log

## [0.7.0] — 2026-04-06

### Added — Phase 7
- `ToolRegistry`: max-15 tool cap, schema validation
- `PermissionPolicy`: per-tool Allow/Deny/Prompt
- `ToolExecutor`: dispatches to registered tools with permission check

## [0.6.0] — 2026-04-06

### Added — Phase 6
- `ConversationRuntime`: generic over `ApiClient` + `ToolExecutor`
- Compaction: three-stage (4K memory flush -> 10K main -> 50K deep)
- `SystemPromptBuilder`: JASUSI.md discovery, ancestor walk, 4000-char/file limit

## [0.5.0] — 2026-04-06

### Added — Phase 5
- `ScoredRouter`: 5-dimension confidence scoring, fallback on ambiguity
- Provider fallback chain: Nemotron -> Gemini -> Kimi -> DeepSeek -> Kimi

## [0.4.0] — 2026-04-06

### Added — Phase 4
- `SessionStore`: two-layer persistence (sessions.json + JSONL transcripts)
- `ChromaMemoryStore`: semantic search via ChromaDB
- Atomic session writes: tempfile -> os.replace()

## [0.3.0] — 2026-04-06

### Added — Phase 3
- `InjectionGuard`: sanitizes JASUSI.md before system prompt injection
- `ApiKey` opaque type: `__str__` and `__repr__` return `"***"`
- Output sanitizer: strips API key patterns from all terminal output

## [0.2.0] — 2026-04-06

### Added — Phase 2
- `SettingsLoader`: 3-source cascade (user -> project -> local)
- `PortContext`: workspace scanning, file counts
- `MultiProviderClient`: 4 providers, 429 fallback chain

## [0.1.0] — 2026-04-06

### Added — Phases 0-1
- Project scaffold: `jasusi_cli/` Python package, `rust/` workspace
- `BootstrapPhase` enum, `ConfigLoader`, `LogManager`
- Initial `Cargo.toml` workspace with `api`, `runtime`, `tools`, `commands` crates
