"""
JasusiCLI Web UI — app.py
FastAPI backend with SSE streaming, file upload, and live quota display.

Run (local development only): uvicorn app:app --host 127.0.0.1 --port 8000

Security posture (see docs/security/findings_traceability.md):
  * The adapter never executes tools in-process. Work is dispatched to the CLI
    as an argv vector — never a shell string and never interpolated source — so
    prompt content cannot alter the command that runs.
  * Authentication fails closed on any non-loopback request.
  * Every stream is bounded in duration, output volume, and concurrency, and is
    cancelled (whole process tree) when the client disconnects.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import AsyncGenerator, Iterable
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from jasusi_cli.config.registry import PROVIDER_ENV_VARS, ROLES, VERSION, roster

load_dotenv()

logger = logging.getLogger(__name__)

app = FastAPI(title="JasusiCLI Web UI", version=VERSION)

# ── Limits ───────────────────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_PROMPT_CHARS = 100_000
MAX_FILENAME_CHARS = 255
#: Hard ceiling on a single streamed run. Bounds worst-case resource hold time.
MAX_STREAM_SECONDS = float(os.environ.get("JASUSI_WEB_MAX_STREAM_SECONDS", "600"))
#: Hard ceiling on emitted lines, so a runaway task cannot exhaust memory.
MAX_STREAM_LINES = 20_000
#: Hard ceiling on a single output line before truncation.
MAX_LINE_CHARS = 8_192
#: Concurrent streaming runs admitted before the adapter returns backpressure.
MAX_CONCURRENT_STREAMS = int(os.environ.get("JASUSI_WEB_MAX_CONCURRENCY", "4"))

PROJECT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

_stream_slots: asyncio.Semaphore | None = None


def _slots() -> asyncio.Semaphore:
    """Lazily bind the semaphore to the running loop."""
    global _stream_slots
    if _stream_slots is None:
        _stream_slots = asyncio.Semaphore(MAX_CONCURRENT_STREAMS)
    return _stream_slots


# ── Errors ───────────────────────────────────────────────────────────────────


class ApiError(HTTPException):
    """HTTPException carrying a stable machine-readable code.

    Internal exception text is never returned to the client; it is logged
    server-side and the client receives a fixed code plus a generic message.
    """

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(status_code, {"code": code, "message": message})


def _internal_error(code: str, exc: BaseException) -> ApiError:
    logger.exception("request failed: code=%s", code, exc_info=exc)
    return ApiError(500, code, "internal error")


# ── Auth ─────────────────────────────────────────────────────────────────────

UI_PASSWORD = os.environ.get("UI_PASSWORD", "")

_PUBLIC_PATHS = frozenset({"/"})


def _is_loopback(host: str | None) -> bool:
    """Whether a client address is loopback. Unknown addresses are not trusted."""
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _with_security_headers(response, *, authenticated: bool):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'",
    )
    if authenticated:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Password gate that fails closed off-loopback.

    Leaving ``UI_PASSWORD`` unset disables the gate for local development only.
    A request that did not originate from loopback is rejected outright when no
    password is configured — the adapter must never serve an unauthenticated
    remote caller merely because the operator forgot to set one.

    Credentials are accepted in the ``x-ui-key`` header only. Query-string
    credentials were removed: they leak into access logs, proxies, and Referer
    headers.
    """
    client_host = request.client.host if request.client else None
    loopback = _is_loopback(client_host)

    if not UI_PASSWORD:
        if not loopback:
            logger.warning(
                "rejected non-loopback request from %s: UI_PASSWORD is not set",
                client_host,
            )
            return JSONResponse(
                {"code": "auth_not_configured", "message": "authentication required"},
                status_code=503,
            )
        return _with_security_headers(await call_next(request), authenticated=False)

    if request.url.path in _PUBLIC_PATHS:
        return _with_security_headers(await call_next(request), authenticated=False)

    token = request.headers.get("x-ui-key", "")
    if not token or not hmac.compare_digest(token, UI_PASSWORD):
        return JSONResponse(
            {"code": "unauthorized", "message": "authentication required"},
            status_code=401,
        )
    return _with_security_headers(await call_next(request), authenticated=True)


LOCAL_ALLOWED_ORIGINS = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=LOCAL_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["x-ui-key", "content-type"],
)


# ── Validation helpers ───────────────────────────────────────────────────────


def validate_project_id(project: str) -> str:
    if not PROJECT_ID_PATTERN.match(project):
        raise ApiError(400, "invalid_project", "invalid project identifier")
    return project


def canonicalize_path(target_path: str | Path, base_dir: Path | None = None) -> Path:
    """Resolve ``target_path`` and prove it stays inside ``base_dir``.

    Uses component-wise containment (``Path.is_relative_to``). A string prefix
    test is not sufficient: ``/srv/work-evil`` starts with ``/srv/work`` yet is
    a different directory.
    """
    base = (base_dir or Path.cwd()).resolve()
    target = Path(target_path)
    resolved = (
        (base / target).resolve() if not target.is_absolute() else target.resolve()
    )
    if not resolved.is_relative_to(base):
        raise ApiError(400, "path_escape", "path resolves outside the permitted root")
    return resolved


def _jasusi_argv(*args: str, project: str) -> list[str]:
    """Build the CLI argv vector.

    Every element is a discrete argument. No shell is involved and no value is
    interpolated into source, so prompt content cannot influence what executes.
    """
    return [sys.executable, "-m", "jasusi_cli.cli.entry", "--project", project, *args]


# ── SSE classification ───────────────────────────────────────────────────────


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
    if (stripped.startswith(("→ ", "-> ", "[Router]", "Routing to"))
            or "role:" in lo):
        return "route"

    # Status messages
    if (stripped.startswith(("[JasusiCLI]", "[jasusi]", "◆", "..."))
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
    if (stripped.startswith(("Warning", "WARN", "⚠"))
            or "quota exhausted" in lo or "rate limit" in lo):
        return "warn"

    # Default: actual LLM token output
    return "token"


# ── Process lifecycle ────────────────────────────────────────────────────────


async def _terminate_tree(process: asyncio.subprocess.Process) -> None:
    """Kill a child and every descendant, then reap it.

    ``Process.kill`` reaches only the direct child. A task that forked workers
    would otherwise keep running after the client vanished.
    """
    if process.returncode is not None:
        return

    killed = False
    try:
        if os.name == "nt":
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/T", "/F", "/PID", str(process.pid),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=10)
        else:
            os.killpg(os.getpgid(process.pid), 9)
        killed = True
    except (TimeoutError, ProcessLookupError, PermissionError, OSError):
        logger.warning("tree kill failed for pid %s; falling back", process.pid)

    if not killed:
        try:
            process.kill()
        except ProcessLookupError:
            pass

    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:
        logger.error("process %s did not exit after tree kill", process.pid)


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _stream_cli(
    argv: list[str],
    request: Request,
    cleanup: Iterable[Path] = (),
) -> AsyncGenerator[str, None]:
    """Run the CLI out-of-process and stream classified events incrementally.

    Events are emitted as the child produces them — the first token is
    observable long before the run completes. The child is terminated (whole
    tree) on client disconnect, timeout, or generator close.
    """
    slots = _slots()
    try:
        await asyncio.wait_for(slots.acquire(), timeout=0.001)
    except TimeoutError:
        yield _sse({"type": "error", "text": "server at capacity, retry shortly"})
        yield _sse({"type": "done", "status": "rejected", "code": 503})
        return

    process: asyncio.subprocess.Process | None = None
    clf_state: dict = {"in_code": False}
    emitted = 0

    # New session/process group so the whole tree is signalable.
    spawn_kwargs: dict = {}
    if os.name == "nt":
        spawn_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        spawn_kwargs["start_new_session"] = True

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            **spawn_kwargs,
        )

        assert process.stdout is not None
        loop = asyncio.get_running_loop()
        deadline = loop.time() + MAX_STREAM_SECONDS

        while True:
            if await request.is_disconnected():
                logger.info("client disconnected; cancelling run")
                yield _sse({"type": "done", "status": "cancelled", "code": 499})
                return

            remaining = deadline - loop.time()
            if remaining <= 0:
                yield _sse({"type": "error", "text": "run exceeded time limit"})
                yield _sse({"type": "done", "status": "timeout", "code": 504})
                return

            try:
                raw = await asyncio.wait_for(
                    process.stdout.readline(), timeout=min(1.0, remaining),
                )
            except TimeoutError:
                # Poll for disconnect/deadline, then keep reading.
                continue

            if not raw:
                break

            line = raw.decode("utf-8", errors="replace").rstrip()
            if not line:
                continue

            emitted += 1
            if emitted > MAX_STREAM_LINES:
                yield _sse({"type": "warn", "text": "output limit reached; truncating"})
                yield _sse({"type": "done", "status": "truncated", "code": 1})
                return

            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + " …[truncated]"

            yield _sse({"type": classify_line(line, clf_state), "text": line})

        await asyncio.wait_for(process.wait(), timeout=10)
        status = "success" if process.returncode == 0 else "error"
        yield _sse({"type": "done", "status": status, "code": process.returncode or 0})

    except asyncio.CancelledError:
        # Generator closed by the server (client vanished mid-write).
        raise
    except Exception:  # noqa: BLE001 — boundary: map to a stable code
        logger.exception("stream failed")
        yield _sse({"type": "error", "text": "internal error"})
        yield _sse({"type": "done", "status": "error", "code": 1})
    finally:
        if process is not None:
            await _terminate_tree(process)
        slots.release()
        for path in cleanup:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("failed to remove %s", path)


# ── API routes ────────────────────────────────────────────────────────────────


def _read_counter(path: str) -> int:
    try:
        with open(path) as f:
            parts = f.read().strip().split(",")
        from datetime import date
        if len(parts) == 2 and parts[0] == str(date.today()):
            return int(parts[1])
    except (OSError, ValueError):
        pass
    return 0


@app.get("/api/status")
async def get_status():
    """Return live quota + API key health for the status panel.

    The roster and version render from the canonical registry so this endpoint
    cannot drift from the CLI, the router, or the documentation.
    """
    def _traffic(used: int, limit: int) -> str:
        pct = used / limit if limit else 0.0
        if pct < 0.9:
            return "green"
        if pct < 0.98:
            return "yellow"
        return "red"

    quota = {}
    for spec in ROLES:
        if spec.daily_request_limit is None:
            continue
        used = _read_counter(os.path.expanduser(f"~/.jasusi_{spec.role}_rpd"))
        quota[spec.role] = {
            "used": used,
            "limit": spec.daily_request_limit,
            "status": _traffic(used, spec.daily_request_limit),
        }

    return {
        "version": VERSION,
        "keys": {
            provider: bool(os.environ.get(env_var))
            for provider, env_var in PROVIDER_ENV_VARS.items()
        },
        "quota": quota,
        "roles": roster(),
    }


class TaskRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=MAX_PROMPT_CHARS)
    project: str = "web"


@app.post("/api/task/stream")
async def stream_task(req: TaskRequest, request: Request):
    """Stream a jasusi task via SSE (POST JSON body)."""
    if not req.prompt.strip():
        raise ApiError(400, "empty_prompt", "prompt required")
    project = validate_project_id(req.project)

    argv = _jasusi_argv("run", req.prompt, project=project)
    return StreamingResponse(
        _stream_cli(argv, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.post("/api/fix/stream")
async def stream_fix(
    request: Request,
    file: UploadFile = File(...),
    project: str = Form("web"),
):
    """Upload a file, run jasusi fix in preview mode, stream output."""
    validated_project = validate_project_id(project)

    safe_filename = Path(file.filename or "fix.py").name
    if len(safe_filename) > MAX_FILENAME_CHARS:
        raise ApiError(400, "filename_too_long", "filename exceeds length limit")
    if not safe_filename or safe_filename in {".", ".."}:
        raise ApiError(400, "invalid_filename", "invalid filename")

    upload_root = Path(tempfile.mkdtemp(prefix="jasusi_fix_"))
    # Containment is proven, not assumed: the destination must resolve inside
    # the freshly created upload root even after symlink resolution.
    destination = canonicalize_path(safe_filename, base_dir=upload_root)

    total_bytes = 0
    try:
        with open(destination, "wb") as handle:
            while chunk := await file.read(65536):
                total_bytes += len(chunk)
                if total_bytes > MAX_UPLOAD_BYTES:
                    raise ApiError(413, "upload_too_large", "upload exceeds 10MB limit")
                handle.write(chunk)
    except ApiError:
        destination.unlink(missing_ok=True)
        raise
    except OSError as exc:
        destination.unlink(missing_ok=True)
        raise _internal_error("upload_failed", exc) from exc

    argv = _jasusi_argv("fix", str(destination), project=validated_project)
    return StreamingResponse(
        _stream_cli(argv, request, cleanup=[destination]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/api/memory")
async def get_memory(project: str = "web"):
    """Return WormLedger entries for the given project."""
    validated_project = validate_project_id(project)
    try:
        from jasusi_cli.core.memory import JasusiMemory

        mem = JasusiMemory(project=validated_project)
        context = mem.load_project_context(query="")
        return {"project": validated_project, "context": context}
    except Exception as exc:  # noqa: BLE001 — boundary
        raise _internal_error("memory_read_failed", exc) from exc


@app.delete("/api/memory")
async def wipe_memory(project: str = "web"):
    """Wipe WormLedger for the given project."""
    validated_project = validate_project_id(project)
    try:
        from jasusi_cli.core.memory import JasusiMemory

        JasusiMemory(project=validated_project).wipe()
        return {"wiped": True, "project": validated_project}
    except Exception as exc:  # noqa: BLE001 — boundary
        raise _internal_error("memory_wipe_failed", exc) from exc


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = Path(__file__).parent / "ui" / "index.html"
    if html_path.exists():
        return HTMLResponse(html_path.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>UI not found — run the build step</h1>", 500)
