from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class HistoryEvent:
    seq: int
    timestamp: str
    session_id: str
    title: str
    detail: str
    tags: list[str] = field(default_factory=list)


class HistoryLog:
    """
    Append-only JSONL history log at ~/.jasusi/history.jsonl
    Renders to Markdown via to_markdown().
    Atomic append via temp-file pattern (Windows safe).
    """

    def __init__(self, log_path: Path | None = None) -> None:
        self._path = log_path or (Path.home() / ".jasusi" / "history.jsonl")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._seq = self._read_max_seq()

    def _read_max_seq(self) -> int:
        if not self._path.exists():
            return 0
        max_seq = 0
        try:
            for raw_line in self._path.read_text(encoding="utf-8").splitlines():
                try:
                    obj = json.loads(raw_line)
                    max_seq = max(max_seq, int(obj.get("seq", 0)))
                except (json.JSONDecodeError, ValueError):
                    pass
        except OSError:
            pass
        return max_seq

    def append(
        self,
        session_id: str,
        title: str,
        detail: str,
        tags: list[str] | None = None,
    ) -> HistoryEvent:
        self._seq += 1
        event = HistoryEvent(
            seq=self._seq,
            timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
            session_id=session_id,
            title=title,
            detail=detail,
            tags=tags or [],
        )
        self._append_atomic(event)
        return event

    def _append_atomic(self, event: HistoryEvent) -> None:
        tmp_path = self._path.with_suffix(".jsonl.tmp")
        existing = ""
        if self._path.exists():
            existing = self._path.read_text(encoding="utf-8")
        line = json.dumps({
            "seq": event.seq,
            "timestamp": event.timestamp,
            "session_id": event.session_id,
            "title": event.title,
            "detail": event.detail,
            "tags": event.tags,
        })
        tmp_path.write_text(existing + line + "\n", encoding="utf-8")
        os.replace(tmp_path, self._path)

    def read_all(self, limit: int = 100) -> list[HistoryEvent]:
        if not self._path.exists():
            return []
        events: list[HistoryEvent] = []
        try:
            lines = [
                raw_line
                for raw_line in self._path.read_text(encoding="utf-8").splitlines()
                if raw_line.strip()
            ]
            for raw_line in lines[-limit:]:
                try:
                    obj = json.loads(raw_line)
                    events.append(HistoryEvent(
                        seq=obj["seq"],
                        timestamp=obj["timestamp"],
                        session_id=obj["session_id"],
                        title=obj["title"],
                        detail=obj["detail"],
                        tags=obj.get("tags", []),
                    ))
                except (KeyError, json.JSONDecodeError):
                    pass
        except OSError:
            pass
        return events

    def read_session(self, session_id: str) -> list[HistoryEvent]:
        return [e for e in self.read_all(limit=1000) if e.session_id == session_id]

    def to_markdown(self, limit: int = 20) -> str:
        events = self.read_all(limit=limit)
        if not events:
            return "# History\n\n_No history entries._\n"
        lines = ["# History\n"]
        for e in reversed(events):
            ts = e.timestamp[:19].replace("T", " ")
            tags_str = " ".join(f"`{t}`" for t in e.tags) if e.tags else ""
            lines.append(f"## {e.seq}. {e.title}")
            lines.append(f"- **Session**: `{e.session_id}`")
            lines.append(f"- **Time**: {ts}")
            if tags_str:
                lines.append(f"- **Tags**: {tags_str}")
            if e.detail:
                lines.append(f"- **Detail**: {e.detail}")
            lines.append("")
        return "\n".join(lines)
