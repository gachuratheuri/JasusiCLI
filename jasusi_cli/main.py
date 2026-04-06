"""Entry point for jasusi_cli Python layer."""

from __future__ import annotations

import logging
import sys
from pathlib import Path


def setup_logging(level: str = "info") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    setup_logging()
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
