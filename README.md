# JasusiCLI v3.3.0

> Zero gravity. One signal. The void is the design.

A local AI CLI and web terminal powered by a 6-role heuristic router.
No login. No cloud dashboard. Your keys, your machine, your terminal.

---

## Architecture

| Role | Model | Provider | Cost |
|------|-------|----------|------|
| Developer | `xiaomi/mimo-v2-flash:free` | OpenRouter | Free |
| Executor | `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter | Free |
| Architect | `moonshotai/kimi-k2.5` | OpenRouter | Paid |
| Researcher | `gemini-2.5-pro` | Google AI Studio | Free tier |
| Reviewer | `deepseek/deepseek-v3.2` | OpenRouter | Paid |
| Compaction | `gemini-2.5-flash-lite` | Google AI Studio | Free tier |

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
jasusi fix myfile.py
```

### 4. Run the Web UI

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
# Open: http://localhost:8000
```

### 5. Share publicly (optional)

Install [ngrok](https://ngrok.com):

```bash
ngrok http 8000
```

Set `UI_PASSWORD` in `.env` before sharing the ngrok URL. Anyone with
the link and password can use the UI against your API keys — quota applies.

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
