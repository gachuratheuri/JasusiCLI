# Changelog

All notable changes to JasusiCLI are documented in this file.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)

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
