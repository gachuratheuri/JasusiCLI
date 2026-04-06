# Jasusi CLI

An AI coding agent with a dual Rust + Python architecture, inspired by the
production-proven design patterns of Claw Code.

## Architecture

```
User Terminal (REPL)
│
▼
┌─────────────────────────────┐
│   Python Orchestration      │  bootstrap → router → query engine → REPL
│   jasusi_cli/ (44 modules)  │  settings · prompt_builder · session_store
│                             │  compaction · tool_pool · history_log
└────────────┬────────────────┘
             │
    ┌────────┼──────────┐
    ▼        ▼          ▼
┌───────┐ ┌────────┐ ┌────────┐
│  api  │ │runtime │ │ tools  │  Rust crates (72.9% of codebase)
│ crate │ │16 mods │ │19 spec │
└───────┘ └────────┘ └────────┘
             │
             ▼
Anthropic / Nemotron / Gemini / Kimi / DeepSeek
```

## Installation

```bash
git clone https://github.com/jasusi/jasusicli
cd jasusicli
pip install -e ".[dev,memory]"

curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
cd rust && cargo build --release
```

## Configuration

Create `~/.jasusi/settings.json`:

```json
{
  "providers": [
    {
      "name": "nemotron",
      "api_key": "your-openai-compat-key",
      "base_url": "https://integrate.api.nvidia.com/v1",
      "models": {
        "developer": "nvidia/llama-3.3-nemotron-super-49b-v1",
        "architect": "nvidia/llama-3.3-nemotron-super-49b-v1"
      }
    },
    {
      "name": "gemini",
      "api_key": "your-gemini-key",
      "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
      "models": {
        "researcher": "gemini-2.5-pro",
        "compaction": "gemini-2.0-flash-lite"
      }
    }
  ]
}
```

Three-source config cascade (lowest to highest priority):
- `~/.jasusi/settings.json` — user-level
- `.claude/settings.json` — project-level (commit to VCS)
- `.claude/settings.local.json` — local overrides (git-ignored, wins all)

## Usage

```bash
jasusi chat                                          # Interactive REPL
jasusi task "implement a BST in Rust with tests"    # One-shot task
jasusi history                                       # Show history log
jasusi version                                       # Show version
jasusi task "find all TODOs" --format ndjson | jq . # Pipeline output
```

## Slash Commands

| Command | Aliases | Description |
|---------|---------|-------------|
| `/help` | `/?` | Show all commands |
| `/status` | | Session ID, model, token counts |
| `/cost` | | Accumulated spend in USD |
| `/model [name]` | | Show or switch current model |
| `/compact` | | Trigger manual compaction |
| `/clear` | `/cls` | Clear conversation history |
| `/permissions` | | Show Allow/Deny/Prompt policy |
| `/resume [id]` | | Resume or list previous sessions |
| `/config` | | Show loaded configuration |
| `/memory` | | Show JASUSI.md for current directory |
| `/init` | | Create JASUSI.md in current directory |
| `/diff` | | Show git diff of session changes |
| `/history [n]` | `/log` | Show last n history events as Markdown |
| `/version` | `/ver` | Show jasusi version |
| `/exit` | `/quit`, `/q` | Exit jasusi |

## Three-Stage Compaction

| Stage | Trigger | Action |
|-------|---------|--------|
| Stage 1 — Soft flush | 4,000 tokens | Silent NO_REPLY turn writes decisions/files to WormLedger (ChromaDB) |
| Stage 2 — Main compact | 10,000 tokens | Strip `<analysis>` tags, preserve 4 recent messages, 160-char summary |
| Stage 3 — Deep compact | 50,000 tokens | Gemini Flash-Lite structured 2,000-token summary written to WormLedger |

## Tool Permissions

| Mode | Default tools |
|------|---------------|
| ALLOW (no prompt) | `file_read`, `glob_search`, `grep_search`, `web_fetch`, `web_search`, `todo_write` |
| PROMPT (ask each time) | `bash`, `file_write`, `file_edit` |
| Simple mode (`--simple`) | Only `bash`, `file_read`, `file_edit` |

## Development

```bash
pytest jasusi_cli/tests/ -v
mypy jasusi_cli/ --strict --ignore-missing-imports
cd rust && cargo check --workspace && cargo test --workspace
cd rust && cargo build --release
```

## Security

- API keys stored in opaque `ApiKey` type — `__str__` returns `"***"`
- Output sanitiser strips `sk-*`, `AIza*`, `GROQ_*`, JWT patterns before
  logging, terminal output, JSONL storage, and ChromaDB insertion
- Session files use atomic `tempfile -> os.replace()` writes
- Tool `input_json` logged as SHA-256 hash only (RULE 9)
- `BashTool` always uses `shell=False` and `timeout=30s` (RULE 3)
- Path traversal guard on all file tools — rejects `../../` escapes
- JSON schema validation on every tool call before execution
