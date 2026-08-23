"""Web adapter security suite.

These tests exist to prove the controls are *reachable*, not merely defined.
Each one exercises the control through the request path a real client takes.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app as web
from app import app as fastapi_app
from app import canonicalize_path, validate_project_id

LOOPBACK = ("127.0.0.1", 51234)
REMOTE = ("203.0.113.7", 51234)


@pytest.fixture
def loopback_client() -> TestClient:
    return TestClient(fastapi_app, client=LOOPBACK)


@pytest.fixture
def remote_client() -> TestClient:
    return TestClient(fastapi_app, client=REMOTE)


# ── Authentication ───────────────────────────────────────────────────────────


def test_non_loopback_request_is_refused_when_no_password_configured(
    remote_client, monkeypatch,
):
    """Fail closed: an unconfigured deployment must not serve remote callers."""
    monkeypatch.setattr(web, "UI_PASSWORD", "")
    response = remote_client.get("/api/status")
    assert response.status_code == 503
    assert response.json()["code"] == "auth_not_configured"


def test_loopback_request_is_allowed_when_no_password_configured(
    loopback_client, monkeypatch,
):
    monkeypatch.setattr(web, "UI_PASSWORD", "")
    assert loopback_client.get("/api/status").status_code == 200


def test_query_string_credential_is_not_accepted(remote_client, monkeypatch):
    """The ``?_key=`` fallback was removed; credentials leak via logs/Referer."""
    monkeypatch.setattr(web, "UI_PASSWORD", "correct-horse")
    response = remote_client.get("/api/status?_key=correct-horse")
    assert response.status_code == 401


def test_header_credential_is_accepted(remote_client, monkeypatch):
    monkeypatch.setattr(web, "UI_PASSWORD", "correct-horse")
    response = remote_client.get(
        "/api/status", headers={"x-ui-key": "correct-horse"},
    )
    assert response.status_code == 200


def test_wrong_credential_is_rejected(remote_client, monkeypatch):
    monkeypatch.setattr(web, "UI_PASSWORD", "correct-horse")
    response = remote_client.get("/api/status", headers={"x-ui-key": "wrong"})
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_security_headers_are_present(loopback_client, monkeypatch):
    monkeypatch.setattr(web, "UI_PASSWORD", "")
    headers = loopback_client.get("/api/status").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "no-referrer"


def test_authenticated_responses_are_not_cached(remote_client, monkeypatch):
    monkeypatch.setattr(web, "UI_PASSWORD", "correct-horse")
    response = remote_client.get(
        "/api/status", headers={"x-ui-key": "correct-horse"},
    )
    assert response.headers["Cache-Control"] == "no-store"


# ── Path containment ─────────────────────────────────────────────────────────


def test_canonicalize_path_rejects_traversal(tmp_path: Path):
    with pytest.raises(web.ApiError) as exc:
        canonicalize_path("../../../etc/passwd", base_dir=tmp_path)
    assert exc.value.status_code == 400


def test_canonicalize_path_rejects_sibling_prefix_directory(tmp_path: Path):
    """``/srv/work-evil`` string-prefixes ``/srv/work`` but is a different tree."""
    base = tmp_path / "work"
    sibling = tmp_path / "work-evil"
    base.mkdir()
    sibling.mkdir()
    target = sibling / "payload.txt"
    target.write_text("x", encoding="utf-8")

    with pytest.raises(web.ApiError) as exc:
        canonicalize_path(target, base_dir=base)
    assert exc.value.status_code == 400


def test_canonicalize_path_accepts_contained_path(tmp_path: Path):
    resolved = canonicalize_path("inner/file.txt", base_dir=tmp_path)
    assert resolved.is_relative_to(tmp_path.resolve())


def test_canonicalize_path_rejects_absolute_escape(tmp_path: Path):
    with pytest.raises(web.ApiError) as exc:
        canonicalize_path(tmp_path.parent / "elsewhere.txt", base_dir=tmp_path)
    assert exc.value.status_code == 400


# ── Input validation ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad", ["../etc/passwd", "web; rm -rf /", "", "a" * 65, "with space"],
)
def test_project_id_validation_rejects_bad_input(bad: str):
    with pytest.raises(web.ApiError):
        validate_project_id(bad)


def test_project_id_validation_accepts_good_input():
    assert validate_project_id("my-project_123") == "my-project_123"


def test_task_stream_rejects_get(loopback_client, monkeypatch):
    monkeypatch.setattr(web, "UI_PASSWORD", "")
    assert loopback_client.get("/api/task/stream?prompt=x").status_code == 405


def test_task_stream_rejects_oversized_prompt(loopback_client, monkeypatch):
    monkeypatch.setattr(web, "UI_PASSWORD", "")
    response = loopback_client.post(
        "/api/task/stream",
        json={"prompt": "x" * (web.MAX_PROMPT_CHARS + 1), "project": "web"},
    )
    assert response.status_code == 422


# ── Command construction ─────────────────────────────────────────────────────


def test_argv_is_a_vector_not_a_shell_string():
    """Prompt content must never be interpolated into executable source."""
    argv = web._jasusi_argv("run", "; rm -rf / #", project="web")
    assert "; rm -rf / #" in argv
    assert argv[1:3] == ["-m", "jasusi_cli.cli.entry"]
    # No element concatenates the payload into a larger command.
    assert not any(
        element.startswith("-c") or "rm -rf" in element
        for element in argv
        if element != "; rm -rf / #"
    )
