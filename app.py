"""
JasusiCLI Web UI — app.py
FastAPI backend with SSE streaming, file upload, and live quota display.
Run: uvicorn app:app --reload --port 8000
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

app = FastAPI(title="JasusiCLI Web UI", version="3.3.0")

# ── Auth ─────────────────────────────────────────────────────────────────────

UI_PASSWORD = os.environ.get("UI_PASSWORD", "")

@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """
    Single-password gate controlled by UI_PASSWORD in .env.
    Leave UI_PASSWORD unset (or empty) to disable auth for local use.
    The login screen sends x-ui-key header on every request after unlock.
    Skips auth on GET / so the login shell always loads.
    Also accepts ?_key=<password> as a fallback for SSE endpoints
    where custom headers cannot be set by EventSource.
    """
    if not UI_PASSWORD:
        return await call_next(request)
    if request.url.path == "/":
        return await call_next(request)
    token = (
        request.headers.get("x-ui-key", "")
        or request.query_params.get("_key", "")
    )
    if not token or token != UI_PASSWORD:
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── helpers ──────────────────────────────────────────────────────────────────

def _jasusi_cmd() -> list[str]:
    """Resolve the jasusi executable from the current venv."""
    return [sys.executable, "-m", "jasusi_cli.cli.entry"]


def _read_counter(path: str) -> int:
    try:
        with open(path) as f:
            parts = f.read().strip().split(",")
        from datetime import date
        if len(parts) == 2 and parts[0] == str(date.today()):
            return int(parts[1])
    except Exception:
        pass
    return 0


def classify_line(line: str, state: dict) -> str:
    """
    Classify a single output line into one of 8 SSE event types.
    state dict persists across calls for the same stream: {"in_code": bool}
    Returns one of: "route" | "status" | "reviewer" | "error" |
                    "warn" | "fence" | "code" | "token"
    """
    stripped = line.strip()
    lo = stripped.lower()

    # Code fence detection (stateful)
    if stripped.startswith("```"):
        state["in_code"] = not state.get("in_code", False)
        return "fence"

    if state.get("in_code", False):
        return "code"

    # Routing signals
    if (stripped.startswith(("\u2192 ", "-> ", "[Router]", "Routing to"))
            or "role:" in lo):
        return "route"

    # Status messages
    if (stripped.startswith(("[JasusiCLI]", "[jasusi]", "\u25c6", "..."))
            or (stripped.startswith("[") and stripped[1:2].isupper())):
        return "status"

    # Reviewer output
    if ("APPROVE" in stripped or "REJECT" in stripped
            or stripped.startswith("Reviewer:")
            or '"approved":' in lo or '"rejected":' in lo):
        return "reviewer"

    # Errors
    if (stripped.startswith(("Error", "ERROR", "Traceback", "Exception"))
            or "failed:" in lo or "FAILED" in lo):
        return "error"

    # Warnings
    if (stripped.startswith(("Warning", "WARN", "\u26a0"))
            or "quota exhausted" in lo or "rate limit" in lo):
        return "warn"

    # Default: actual LLM token output
    return "token"


async def _stream_process(cmd: list[str]) -> AsyncGenerator[str, None]:
    """
    Run a subprocess, classify each output line into a typed SSE event,
    and yield structured JSON events.

    Event schema:
      {"type": "route",    "text": str}   \u2014 routing decision
      {"type": "status",   "text": str}   \u2014 jasusi status message
      {"type": "reviewer", "text": str}   \u2014 reviewer approve/reject
      {"type": "error",    "text": str}   \u2014 error / traceback line
      {"type": "warn",     "text": str}   \u2014 quota / rate-limit warning
      {"type": "fence",    "text": str}   \u2014 code fence (``` line)
      {"type": "code",     "text": str}   \u2014 code content line
      {"type": "token",    "text": str}   \u2014 LLM answer text
      {"type": "done",     "status": str, "code": int}  \u2014 terminal event
    """
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )

    clf_state: dict = {"in_code": False}

    try:
        async for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue
            event_type = classify_line(line, clf_state)
            payload = json.dumps({"type": event_type, "text": line})
            yield f"data: {payload}\n\n"
        await process.wait()
        status = "success" if process.returncode == 0 else "error"
        yield f'data: {json.dumps({"type": "done", "status": status, "code": process.returncode})}\n\n'
    except asyncio.CancelledError:
        process.kill()
        raise


# ── API routes ────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    """Return live quota + API key health for the status panel."""
    dev_used  = _read_counter(os.path.expanduser("~/.jasusi_developer_rpd"))
    res_used  = _read_counter(os.path.expanduser("~/.jasusi_researcher_rpd"))

    def _traffic(used, limit):
        pct = used / limit
        if pct < 0.9:  return "green"
        if pct < 0.98: return "yellow"
        return "red"

    return {
        "version": "3.3.0",
        "keys": {
            "openrouter": bool(os.environ.get("OPENROUTER_API_KEY")),
            "google_ai":  bool(os.environ.get("GOOGLE_AI_STUDIO_KEY")),
        },
        "quota": {
            "developer":  {"used": dev_used,  "limit": 500,   "status": _traffic(dev_used,  500)},
            "researcher": {"used": res_used,  "limit": 100,   "status": _traffic(res_used,  100)},
            "compaction": {"used": 0,         "limit": 1000,  "status": "green"},
        },
        "roles": [
            {"role": "Developer",  "model": "gemini-2.5-flash",              "provider": "Google AI"},
            {"role": "Executor",   "model": "nemotron-3-super-120b:free",    "provider": "OpenRouter"},
            {"role": "Architect",  "model": "kimi-k2.5",                    "provider": "OpenRouter"},
            {"role": "Researcher", "model": "gemini-2.5-pro",               "provider": "Google AI"},
            {"role": "Reviewer",   "model": "deepseek-v3.2",                "provider": "OpenRouter"},
            {"role": "Compaction", "model": "gemini-2.5-flash-lite",        "provider": "Google AI"},
        ],
    }


@app.get("/api/task/stream")
async def stream_task(
    prompt:  str = Query(...,       description="Task prompt"),
    project: str = Query("web",    description="Project name"),
    _key:    str = Query("",       description="Auth key (SSE fallback)"),
):
    """Stream a jasusi task via SSE."""
    if not prompt.strip():
        raise HTTPException(400, "prompt required")
    # Call the orchestrator directly — the CLI entry point swallows output
    import shlex
    escaped_prompt = prompt.replace("\\", "\\\\").replace("'", "\\'")
    escaped_project = project.replace("\\", "\\\\").replace("'", "\\'")
    script = (
        "import sys; sys.path.insert(0,'.'); "
        "from jasusi_cli.core.orchestrator import run_task; "
        f"result = run_task('{escaped_prompt}', project='{escaped_project}'); "
        "print(result)"
    )
    cmd = [sys.executable, "-u", "-c", script]
    return StreamingResponse(
        _stream_process(cmd),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/fix/stream")
async def stream_fix(
    file:    UploadFile = File(...),
    project: str        = Form("web"),
    _key:    str        = Query("",  description="Auth key (SSE fallback)"),
):
    """Upload a file, run jasusi fix on it, stream the output."""
    suffix = Path(file.filename).suffix or ".py"
    with tempfile.NamedTemporaryFile(
        delete=False, suffix=suffix, prefix="jasusi_fix_"
    ) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name

    escaped_path = tmp_path.replace("\\", "\\\\").replace("'", "\\'")
    escaped_project = project.replace("\\", "\\\\").replace("'", "\\'")
    script = (
        "import sys; sys.path.insert(0,'.'); "
        "from jasusi_cli.core.orchestrator import run_fix; "
        f"result = run_fix('{escaped_path}', project='{escaped_project}'); "
        "print(result)"
    )
    cmd = [sys.executable, "-u", "-c", script]

    async def _cleanup_stream():
        try:
            async for chunk in _stream_process(cmd):
                yield chunk
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return StreamingResponse(
        _cleanup_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/memory")
async def get_memory(project: str = "web"):
    """Return WormLedger entries for the given project."""
    try:
        sys.path.insert(0, ".")
        from jasusi_cli.core.memory import JasusiMemory
        mem = JasusiMemory(project=project)
        context = mem.load_project_context(query="")
        return {"project": project, "context": context}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.delete("/api/memory")
async def wipe_memory(project: str = "web"):
    """Wipe WormLedger for the given project."""
    try:
        sys.path.insert(0, ".")
        from jasusi_cli.core.memory import JasusiMemory
        JasusiMemory(project=project).wipe()
        return {"wiped": True, "project": project}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "ui" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>UI not found — run the build step</h1>", 500)
