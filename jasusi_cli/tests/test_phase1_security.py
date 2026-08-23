"""Phase 1 Security & Containment Test Suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import app, canonicalize_path, validate_project_id
from jasusi_cli.tools.permissions import PermissionPolicy, TerminalPrompter

# A loopback client address is required: the adapter now refuses non-loopback
# callers outright when no password is configured (see tests/test_web_security.py).
client = TestClient(app, client=("127.0.0.1", 51234))


def test_web_task_stream_rejects_get():
    """Verify that GET /api/task/stream is rejected (must be POST)."""
    response = client.get("/api/task/stream?prompt=test")
    assert response.status_code == 405  # Method Not Allowed


def test_web_project_id_validation():
    """Verify project ID validation rejects invalid characters and path traversal patterns."""
    assert validate_project_id("web") == "web"
    assert validate_project_id("my-project_123") == "my-project_123"

    with pytest.raises(HTTPException) as exc_info:
        validate_project_id("../etc/passwd")
    assert exc_info.value.status_code == 400

    with pytest.raises(HTTPException) as exc_info:
        validate_project_id("web; rm -rf /")
    assert exc_info.value.status_code == 400


def test_web_path_traversal_canonicalization():
    """Verify path canonicalization rejects paths outside workspace root."""
    cwd = Path.cwd().resolve()
    safe_path = canonicalize_path("README.md")
    assert str(safe_path).startswith(str(cwd))

    with pytest.raises(HTTPException) as exc_info:
        canonicalize_path("../../../etc/passwd")
    assert exc_info.value.status_code == 400


def test_terminal_prompter_fails_closed_in_non_interactive_mode(monkeypatch):
    """Verify TerminalPrompter automatically denies when stdin is not a TTY."""
    prompter = TerminalPrompter()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert prompter.ask("bash", "rm -rf /") is False


def test_permission_policy_prompt_mode_fails_closed_in_non_interactive_mode(monkeypatch):
    """Verify PermissionPolicy in PROMPT mode fails closed without interactive TTY."""
    policy = PermissionPolicy()
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert policy.check("bash", "ls -la") is False
    assert policy.check("file_write", "test.py") is False
