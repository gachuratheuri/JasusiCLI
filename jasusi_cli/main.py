"""Entry point for jasusi_cli Python layer."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path


def setup_logging(level: str = "info") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _print_status() -> None:
    """Display API key status, v3.3 model roster, and daily quota."""
    keys = [
        ("OPENROUTER_API_KEY",   "OpenRouter (Executor / Architect / Reviewer)"),
        ("GOOGLE_AI_STUDIO_KEY", "Google AI Studio (Developer / Researcher / Compaction)"),
    ]
    print("=== JasusiCLI v3.3.0 — API Key Status ===\n")
    for env_var, label in keys:
        val = os.environ.get(env_var)
        if val:
            masked = val[:8] + "..." + val[-4:]
            print(f"  [ok]  {env_var} → {label}")
            print(f"        Key: {masked}")
        else:
            print(f"  [!!]  {env_var} → {label}")
            print(f"        NOT SET")
        print()

    print("=== Model Roster ===\n")
    roster = [
        ("Developer",  "gemini-2.5-flash",                      "Google AI Studio"),
        ("Executor",   "nvidia/nemotron-3-super-120b-a12b:free", "OpenRouter"),
        ("Architect",  "moonshotai/kimi-k2.5",                  "OpenRouter"),
        ("Reviewer",   "deepseek/deepseek-v3.2",                "OpenRouter"),
        ("Researcher", "gemini-2.5-pro",                        "Google AI Studio"),
        ("Compaction", "gemini-2.5-flash-lite",                 "Google AI Studio"),
    ]
    print(f"  {'Role':<12} {'Model':<40} {'Provider'}")
    print(f"  {'-'*12} {'-'*40} {'-'*20}")
    for role, model, provider in roster:
        print(f"  {role:<12} {model:<40} {provider}")
    print()

    # Daily quota
    from jasusi_cli.core.clients import get_developer_rpd
    quota = get_developer_rpd()
    dev = quota["developer_flash_rpd"]
    res = quota["researcher_pro_rpd"]

    def _indicator(used: int, warn: int, crit: int) -> str:
        if used >= crit:
            return "\U0001f534"  # red
        if used >= warn:
            return "\U0001f7e1"  # yellow
        return "\U0001f7e2"  # green

    print("=== Daily Quota ===\n")
    print(f"  {'Role':<20} {'Usage':<16} {'Status'}")
    print(f"  {'-'*20} {'-'*16} {'-'*6}")
    print(f"  {'Developer (Flash)':<20} {dev['used']}/{dev['limit']} RPD"
          f"{'':>5}{_indicator(dev['used'], 450, 490)}")
    print(f"  {'Researcher (Pro)':<20} {res['used']}/{res['limit']} RPD"
          f"{'':>6}{_indicator(res['used'], 85, 95)}")
    print()


def main(argv: list[str] | None = None) -> int:
    setup_logging()

    args = argv or sys.argv[1:]

    if "status" in args:
        _print_status()
        return 0

    from jasusi_cli.bootstrap.graph import BootstrapGraph

    try:
        graph = BootstrapGraph(project_root=Path.cwd())
        result = graph.run(argv)
        logging.getLogger(__name__).info(
            "Bootstrap complete: mode=%s project=%s",
            result.mode.value,
            result.project_root,
        )
        return 0
    except Exception as e:
        logging.getLogger(__name__).error("Bootstrap failed: %s", e)
        return 1


if __name__ == "__main__":
    sys.exit(main())
