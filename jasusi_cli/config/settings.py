"""Three-source cascade config: user → project → local (local wins)"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProviderConfig:
    name: str
    api_key_env: str
    base_url: str
    default_model: str
    enabled: bool = True


@dataclass
class CompactionConfig:
    memory_flush_tokens: int = 4_000
    main_compaction_tokens: int = 10_000
    deep_compaction_tokens: int = 50_000
    preserve_recent: int = 4
    max_summary_chars: int = 160


@dataclass
class SessionConfig:
    prune_after_days: int = 30
    max_entries: int = 500
    rotate_bytes: int = 10 * 1024 * 1024  # 10MB


@dataclass
class JasusiSettings:
    providers: list[ProviderConfig] = field(default_factory=list)
    compaction: CompactionConfig = field(default_factory=CompactionConfig)
    session: SessionConfig = field(default_factory=SessionConfig)
    max_turns: int = 8
    max_budget_tokens: int = 2_000
    compact_after_turns: int = 12
    log_level: str = "info"
    simple_mode: bool = False
    jasusi_md_max_chars: int = 4_000
    jasusi_md_total_max_chars: int = 12_000

    @classmethod
    def default_providers(cls) -> list[ProviderConfig]:
        return [
            ProviderConfig(
                name="nemotron",
                api_key_env="NVIDIA_API_KEY",
                base_url="https://integrate.api.nvidia.com/v1",
                default_model="nvidia/llama-3.3-nemotron-super-49b-v1",
            ),
            ProviderConfig(
                name="gemini",
                api_key_env="GEMINI_API_KEY",
                base_url="https://generativelanguage.googleapis.com/v1beta",
                default_model="gemini-2.5-pro",
            ),
            ProviderConfig(
                name="kimi",
                api_key_env="MOONSHOT_API_KEY",
                base_url="https://api.moonshot.cn/v1",
                default_model="moonshot-v1-128k",
            ),
            ProviderConfig(
                name="deepseek",
                api_key_env="DEEPSEEK_API_KEY",
                base_url="https://api.deepseek.com/v1",
                default_model="deepseek-reasoner",
            ),
        ]


class SettingsLoader:
    """Loads and merges settings from three sources. Local wins."""

    USER_PATH = Path.home() / ".claude" / "settings.json"

    @staticmethod
    def project_path(cwd: Path | None = None) -> Path:
        root = cwd or Path.cwd()
        return root / ".claude" / "settings.json"

    @staticmethod
    def local_path(cwd: Path | None = None) -> Path:
        root = cwd or Path.cwd()
        return root / ".claude" / "settings.local.json"

    @classmethod
    def load(cls, cwd: Path | None = None) -> JasusiSettings:
        merged: dict[str, object] = {}
        for path in [
            cls.USER_PATH,
            cls.project_path(cwd),
            cls.local_path(cwd),
        ]:
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    merged = cls._deep_merge(merged, data)
                except (json.JSONDecodeError, OSError):
                    pass

        settings = JasusiSettings(
            providers=JasusiSettings.default_providers()
        )

        if "max_turns" in merged:
            settings.max_turns = int(str(merged["max_turns"]))
        if "max_budget_tokens" in merged:
            settings.max_budget_tokens = int(str(merged["max_budget_tokens"]))
        if "compact_after_turns" in merged:
            settings.compact_after_turns = int(str(merged["compact_after_turns"]))
        if "log_level" in merged:
            settings.log_level = str(merged["log_level"])
        if "simple_mode" in merged:
            settings.simple_mode = bool(merged["simple_mode"])

        return settings

    @staticmethod
    def _deep_merge(base: dict[str, object], override: dict[str, object]) -> dict[str, object]:
        result = base.copy()
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = SettingsLoader._deep_merge(result[key], value)  # type: ignore[arg-type]
            else:
                result[key] = value
        return result
