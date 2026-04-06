from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from pathlib import Path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jasusi", description="Jasusi — AI coding agent",
    )
    parser.add_argument("--version", action="store_true", help="Show version and exit")
    parser.add_argument(
        "--format", choices=["text", "json", "ndjson"], default="text",
    )
    parser.add_argument(
        "--simple", action="store_true", help="Simple mode: 3 tools only",
    )
    parser.add_argument(
        "--remote", action="store_true", help="Remote execution mode",
    )
    parser.add_argument("--session", metavar="SESSION_ID", help="Resume a session")
    parser.add_argument("--project", default="default", help="Project name")
    parser.add_argument(
        "--log-level",
        default="warning",
        choices=["debug", "info", "warning", "error"],
    )
    sub = parser.add_subparsers(dest="command")
    task_p = sub.add_parser("task", help="Run a one-shot task")
    task_p.add_argument("input", nargs="+", help="Task description")
    sub.add_parser("chat", help="Start interactive REPL")
    sub.add_parser("status", help="Show session status")
    sub.add_parser("history", help="Show history log")
    sub.add_parser("version", help="Show version")
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.WARNING),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )
    if args.version or args.command == "version":
        from jasusi_cli.cli.commands import CommandHandler

        print(f"jasusi v{CommandHandler.VERSION}")
        return 0
    if args.command == "history":
        from jasusi_cli.cli.history import HistoryLog

        log = HistoryLog()
        print(log.to_markdown())
        return 0
    if args.command == "status":
        from jasusi_cli.bootstrap.graph import BootstrapGraph

        ctx = BootstrapGraph(cwd=Path.cwd()).run_status_fast_path()
        store = ctx.session_store
        if store is not None:
            sessions = store.list_sessions()
            print(f"Sessions: {len(sessions)}")
            for s in sessions[:10]:
                print(f"  {s.session_id}  {s.project}")
        else:
            print("No session store available.")
        return 0
    if args.command in ("chat", None, "task"):
        from jasusi_cli.cli.output import OutputFormat

        fmt_map: dict[str, OutputFormat] = {
            "text": OutputFormat.TEXT,
            "json": OutputFormat.JSON,
            "ndjson": OutputFormat.NDJSON,
        }
        fmt = fmt_map.get(args.format, OutputFormat.TEXT)
        session_id: str = args.session or str(uuid.uuid4())[:12]

        from jasusi_cli.cli.repl import Repl

        repl = Repl(
            session_id=session_id,
            project=args.project,
            output_format=fmt,
            cwd=Path.cwd(),
        )
        if args.command == "task":
            task_text = " ".join(args.input)
            return asyncio.run(_run_task(repl, task_text))
        try:
            asyncio.run(repl.run())
        except KeyboardInterrupt:
            pass
        return 0
    parser.print_help()
    return 1


async def _run_task(repl: object, task_text: str) -> int:
    from jasusi_cli.cli.repl import Repl

    assert isinstance(repl, Repl)
    await repl._process_turn(task_text)
    repl._formatter.flush_json()
    return 0


def main() -> None:
    sys.exit(run_cli())
