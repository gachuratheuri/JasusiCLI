# JasusiCLI v3 — Persistent Agent Constraints

## ABSOLUTE RULES
1. NEVER use todo!(), unimplemented!(), or // TODO. All implementations must be complete.
2. NEVER invent crate versions. Use ONLY the versions specified in the active prompt.
3. NEVER use SeccompAction::Errno — always SeccompAction::KillProcess for bash sandboxing.
4. NEVER return Err for Landlock unavailability — return Ok(LandlockStatus::Unavailable).
5. ToolRequest.input_json MUST be bytes in proto, NEVER string.
6. on_stop in Kameo actors MUST use append_blocking(), NOT await ledger.append().
7. Run cargo clippy -- -D warnings after EVERY file you create. Fix all warnings before proceeding.
8. UDS socket path is /tmp/jasusi-{uid}.sock where uid = std::process::id(). Never hardcode it.
9. Never store raw secrets or raw input_json in the audit ledger — only SHA-256(input_json).
10. SystemPromptBuilder.build_turn() MUST assert fnv1a_hash(static_block) == self._static_hash every call.

## ACTUAL CRATE NAMES (confirmed from cargo check)
- rusty-claude-cli        → RENAME TO: jasusi-core
- api                     → EXTEND (multi-provider client)
- runtime                 → EXTEND (Kameo actors + compaction)
- tools                   → EXTEND (sandbox enforcement)
- commands                → EXTEND (add /ledger /rollback)
- compat-harness          → PRESERVE unchanged
- mock-anthropic-service  → PRESERVE (useful for tests)
- plugins    → PRESERVE unchanged
- telemetry  → PRESERVE unchanged

## CARGO VERSIONS (do not deviate)
tokio="1.38"(full) kameo="0.15"(derive,remote) tonic="0.12"(transport)
prost="0.13" buffa="0.2" aya="0.13"(async_tokio) seccompiler="0.4"
landlock="0.4" qdrant-client="1.11"(async) rusqlite="0.31"(bundled)
sha2="0.10" jsonschema-rs="0.18" serde="1"(derive) unicode-normalization="0.1"

## POWERSHELL COMMAND TRANSLATIONS (Windows environment)
tail -N        → Select-Object -Last N
head -N        → Select-Object -First N
grep "x"       → Select-String "x"
grep -c "x"    → (Select-String "x" | Measure-Object).Count
mkdir -p path  → New-Item -ItemType Directory -Force path
touch file     → New-Item -Force file
rm -rf dir     → Remove-Item dir -Recurse -Force
ls             → Get-ChildItem
chmod          → NOT APPLICABLE on Windows