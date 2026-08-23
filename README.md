# JasusiCLI v3.0.0

> Zero gravity. One signal. The void is the design.

A local AI CLI and web terminal powered by a 6-role heuristic router.
No login. No cloud dashboard. Your keys, your machine, your terminal.

---

## Architecture

The role → model → provider mapping is defined once, in
[`jasusi_cli/config/registry.py`](jasusi_cli/config/registry.py). The CLI
(`jasusi status`), the web status endpoint, and the router all render from it.
Run `jasusi status` for the roster that is actually in effect; the table below
is illustrative and can drift.

| Role | Model | Provider |
|------|-------|----------|
| Developer | `gemini-2.5-flash` | Google AI Studio |
| Executor | `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter |
| Architect | `moonshotai/kimi-k2.5` | OpenRouter |
| Researcher | `gemini-2.5-pro` | Google AI Studio |
| Reviewer | `deepseek/deepseek-v3.2` | OpenRouter |
| Compaction | `gemini-2.5-flash-lite` | Google AI Studio |

Routing is heuristic — zero LLM calls, O(n) scoring across 6 dimensions.

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/<your-username>/jasusicli-v3.git
cd jasusicli-v3
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
pip install fastapi "uvicorn[standard]" python-multipart
```

### 2. Configure keys

```bash
cp .env.template .env
```

Edit `.env`:

```env
OPENROUTER_API_KEY=sk-or-...
GOOGLE_AI_STUDIO_KEY=AIza...
UI_PASSWORD=                  # optional — leave empty for local-only use
```

### 3. Run the CLI

```bash
jasusi task "refactor this function to use list comprehension"
jasusi fix myfile.py     # proposes a fix; never writes to the file
```

> [!IMPORTANT]
> **Execution is fail-closed.** Shell and file-write tools are denied when the
> host provides no effective OS isolation. Linux with user namespaces, or any
> containerised environment, satisfies this. On other hosts — including
> Windows and macOS today — pass `--unsafe-local-mode` to accept the risk
> explicitly, or run inside a container.
>
> `jasusi sandbox` reports what is actually enforced on your machine.

### 4. Run the Web UI (LOCAL DEVELOPMENT ONLY)

> [!WARNING]
> **Security Containment Notice (Phase 0 Governance Active)**:
> Remote exposure, public tunneling (e.g. ngrok, Cloudflare Tunnels), and binding to non-loopback network interfaces (`0.0.0.0`) are **STRICTLY SUSPENDED** until Phase 3 (Gate G3) security verification completes. The web interface MUST ONLY be run locally on `127.0.0.1`.

```bash
uvicorn app:app --reload --host 127.0.0.1 --port 8000
# Open: http://127.0.0.1:8000
```

### 5. Remote Exposure (SUSPENDED)

Public tunneling and remote exposure features are currently **DISABLED** pending Phase 3 security kernel implementation and formal authorization controls. Do not expose this service on public networks or tunnels.


---

## Web UI Panels

| Panel | Purpose |
|-------|---------|
| **Task** | Stream any prompt through the ScoredRouter → specialist model |
| **Fix File** | Upload a file → Developer rewrites → Reviewer approves/rejects |
| **Memory** | View and wipe the WormLedger ChromaDB context per project |
| **Status** | Live key health, model roster, RPD quota bars |

---

## SSE Event Protocol

Every task produces a typed stream of events:

| Type | Color | Meaning |
|------|-------|---------|
| `route` | Purple | Routing decision (→ Developer, score: 0.82) |
| `status` | Faint | JasusiCLI internal status |
| `code` | Cyan | Code content (inside ``` fences) |
| `token` | Green | LLM answer text |
| `fence` | Faint | Code fence delimiters |
| `warn` | Yellow | Quota / rate-limit warnings |
| `error` | Red | Error lines |
| `reviewer` | Yellow | Reviewer approve / reject |

---

## Fallback Chain

`xiaomi/mimo-v2-flash` → `nvidia/nemotron-3-super-120b` → `mistralai/devstral` → `qwen/qwen3-coder`

---

## What is NOT in this repo

- `.env` — your API keys (never committed)
- `.jasusi/` — local ChromaDB memory store
- `*.log` — uvicorn logs

---

## License

MIT
