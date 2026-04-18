"""
Centralised API client factory for JasusiCLI v3.3.
Two providers: OpenRouter (openai-compatible) + Google AI Studio (openai-compatible).
"""

import os
import json
from datetime import date as _date
from pathlib import Path
from typing import Any
from openai import OpenAI

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

_SETTINGS_PATH = Path(__file__).parent.parent.parent / "settings.json"


def _load_settings() -> dict[str, Any]:
    with open(_SETTINGS_PATH) as f:
        result: dict[str, Any] = json.load(f)
        return result


def _get_openrouter_client() -> OpenAI:
    settings = _load_settings()
    cfg = settings["providers"]["openrouter"]
    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        raise EnvironmentError(
            f"Missing env var: {cfg['api_key_env']}. "
            "Get your key at https://openrouter.ai/keys"
        )
    return OpenAI(
        base_url=cfg["base_url"],
        api_key=api_key,
        default_headers=cfg.get("extra_headers", {}),
    )


def _get_googleai_client() -> OpenAI:
    settings = _load_settings()
    cfg = settings["providers"]["googleai"]
    api_key = os.environ.get(cfg["api_key_env"])
    if not api_key:
        raise EnvironmentError(
            f"Missing env var: {cfg['api_key_env']}. "
            "Get your key at https://aistudio.google.com/app/apikey"
        )
    return OpenAI(
        base_url=cfg["base_url"],
        api_key=api_key,
    )


def get_client(provider: str) -> OpenAI:
    if provider == "openrouter":
        return _get_openrouter_client()
    elif provider == "googleai":
        return _get_googleai_client()
    raise ValueError(f"Unknown provider: {provider}")


def get_model(role: str) -> tuple[str, str]:
    """Returns (model_id, provider) for a given role name."""
    settings = _load_settings()
    cfg = settings["routing"][role]
    return cfg["model"], cfg["provider"]


def get_fallback_chain() -> list[dict[str, str]]:
    """Returns the OpenRouter fallback chain from settings.json."""
    settings = _load_settings()
    result: list[dict[str, str]] = settings["fallback_chain"]
    return result


# ── RPD counters ──────────────────────────────────────────────────────────────

_DEVELOPER_COUNTER = os.path.expanduser("~/.jasusi_developer_rpd")
_RESEARCHER_COUNTER = os.path.expanduser("~/.jasusi_researcher_rpd")


def _read_counter(path: str) -> int:
    """Read today's RPD count from a counter file."""
    try:
        with open(path) as f:
            parts = f.read().strip().split(",")
        if len(parts) == 2 and parts[0] == str(_date.today()):
            return int(parts[1])
    except (FileNotFoundError, ValueError):
        pass
    return 0


def _increment_counter(path: str) -> int:
    """Increment today's RPD count. Returns new count."""
    count = _read_counter(path) + 1
    with open(path, "w") as f:
        f.write(f"{_date.today()},{count}")
    return count


def get_developer_rpd() -> dict[str, dict[str, int]]:
    """
    Returns current RPD usage for Developer + Researcher
    so jasusi status can show live quota consumption.
    """
    return {
        "developer_flash_rpd": {
            "used": _read_counter(_DEVELOPER_COUNTER),
            "limit": 500,
        },
        "researcher_pro_rpd": {
            "used": _read_counter(_RESEARCHER_COUNTER),
            "limit": 100,
        },
    }
