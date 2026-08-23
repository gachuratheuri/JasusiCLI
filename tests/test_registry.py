"""Tests for canonical registry and settings.json synchrony."""

from __future__ import annotations

import json
from pathlib import Path

from jasusi_cli.config.registry import (
    _FALLBACK_VERSION,
    ROLES,
    ROLES_BY_NAME,
    ROUTABLE_ROLES,
    VERSION,
    model_for,
    provider_for,
    roster,
)


def test_registry_roles_match_settings_routing() -> None:
    settings_path = Path(__file__).parent.parent / "settings.json"
    with open(settings_path, encoding="utf-8") as f:
        settings = json.load(f)

    routing = settings["routing"]
    for role_name, spec in ROLES_BY_NAME.items():
        assert role_name in routing, f"Role {role_name} missing from settings.json routing"
        assert routing[role_name]["model"] == spec.model, (
            f"Model mismatch for role {role_name}: {routing[role_name]['model']} vs {spec.model}"
        )


def test_executor_model_is_nemotron_ultra() -> None:
    assert ROLES_BY_NAME["executor"].model == "nvidia/nemotron-3-ultra-550b-a35b:free"
    assert model_for("executor") == "nvidia/nemotron-3-ultra-550b-a35b:free"
    assert provider_for("executor") == "openrouter"


def test_version_string_consistency() -> None:
    settings_path = Path(__file__).parent.parent / "settings.json"
    with open(settings_path, encoding="utf-8") as f:
        settings = json.load(f)

    assert settings["system"]["version"] == "3.3.0"
    assert _FALLBACK_VERSION == "3.0.0"
    assert VERSION == "3.0.0"


def test_roster_renders_all_roles() -> None:
    r = roster()
    assert len(r) == len(ROLES)
    roles_rendered = {entry["role"].lower() for entry in r}
    assert "executor" in roles_rendered
    assert "developer" in roles_rendered
    assert "architect" in roles_rendered
    assert "researcher" in roles_rendered
    assert "reviewer" in roles_rendered
    assert "compaction" in roles_rendered


def test_routable_roles_excludes_compaction() -> None:
    assert "compaction" not in ROUTABLE_ROLES
    assert "developer" in ROUTABLE_ROLES
    assert "executor" in ROUTABLE_ROLES
