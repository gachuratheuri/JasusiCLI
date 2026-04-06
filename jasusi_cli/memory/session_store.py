"""Python session store — lightweight mirror of Rust memory::session_store."""

from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass
from enum import Enum, auto
from pathlib import Path


class ContentBlockType(Enum):
    TEXT = auto()
    TOOL_USE = auto()
    TOOL_RESULT = auto()


@dataclass
class ContentBlock:
    block_type: ContentBlockType
    content: str
    is_error: bool = False


@dataclass
class TranscriptEntry:
    role: str
    content: list[ContentBlock]
    timestamp: str
    turn_seq: int


@dataclass
class SessionMeta:
    session_id: str
    project: str
    created_at: str
    updated_at: str
    input_tokens: int = 0
    output_tokens: int = 0
    compaction_count: int = 0
    turn_count: int = 0


class SessionStore:
    def __init__(self, base_dir: Path) -> None:
        self._base_dir = base_dir
        self._index: dict[str, SessionMeta] = {}
        base_dir.mkdir(parents=True, exist_ok=True)
        self._load_index()

    @classmethod
    def open(cls, base_dir: Path) -> SessionStore:
        return cls(base_dir)

    @classmethod
    def default_path(cls) -> Path:
        return Path.home() / ".jasusi" / "sessions"

    def _index_path(self) -> Path:
        return self._base_dir / "sessions.json"

    def _transcript_path(self, session_id: str) -> Path:
        return self._base_dir / f"{session_id}.jsonl"

    def _load_index(self) -> None:
        p = self._index_path()
        if p.exists():
            try:
                data: dict[str, dict[str, object]] = json.loads(
                    p.read_text(encoding="utf-8")
                )
                for k, v in data.items():
                    self._index[k] = SessionMeta(
                        session_id=str(v.get("session_id", k)),
                        project=str(v.get("project", "")),
                        created_at=str(v.get("created_at", "")),
                        updated_at=str(v.get("updated_at", "")),
                        input_tokens=int(str(v.get("input_tokens", 0))),
                        output_tokens=int(str(v.get("output_tokens", 0))),
                        compaction_count=int(str(v.get("compaction_count", 0))),
                        turn_count=int(str(v.get("turn_count", 0))),
                    )
            except Exception:
                pass

    def _flush_index(self) -> None:
        tmp = self._base_dir / "sessions.json.tmp"
        data: dict[str, dict[str, object]] = {
            k: {
                "session_id": v.session_id,
                "project": v.project,
                "created_at": v.created_at,
                "updated_at": v.updated_at,
                "input_tokens": v.input_tokens,
                "output_tokens": v.output_tokens,
                "compaction_count": v.compaction_count,
                "turn_count": v.turn_count,
            }
            for k, v in self._index.items()
        }
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, self._index_path())

    def create_session(self, session_id: str, project: str) -> SessionMeta:
        now = datetime.datetime.now(datetime.UTC).isoformat()
        meta = SessionMeta(
            session_id=session_id, project=project,
            created_at=now, updated_at=now,
        )
        self._index[session_id] = meta
        self._flush_index()
        return meta

    def get_session(self, session_id: str) -> SessionMeta | None:
        return self._index.get(session_id)

    def update_tokens(
        self, session_id: str, input_tokens: int, output_tokens: int,
    ) -> None:
        meta = self._index.get(session_id)
        if meta is not None:
            meta.input_tokens += input_tokens
            meta.output_tokens += output_tokens
            meta.turn_count += 1
            meta.updated_at = datetime.datetime.now(datetime.UTC).isoformat()
            self._flush_index()

    def increment_compaction(self, session_id: str) -> None:
        meta = self._index.get(session_id)
        if meta is not None:
            meta.compaction_count += 1
            meta.updated_at = datetime.datetime.now(datetime.UTC).isoformat()
            self._flush_index()

    def append_transcript(self, session_id: str, entry: TranscriptEntry) -> None:
        path = self._transcript_path(session_id)
        line = json.dumps({
            "role": entry.role,
            "content": [
                {
                    "block_type": b.block_type.name,
                    "content": b.content,
                    "is_error": b.is_error,
                }
                for b in entry.content
            ],
            "timestamp": entry.timestamp,
            "turn_seq": entry.turn_seq,
        })
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def read_transcript(
        self, session_id: str, limit: int = 100,
    ) -> list[TranscriptEntry]:
        path = self._transcript_path(session_id)
        if not path.exists():
            return []
        lines = [
            line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        entries: list[TranscriptEntry] = []
        for line in lines[-limit:]:
            try:
                d: dict[str, object] = json.loads(line)
                raw_content = d.get("content", [])
                content_list: list[ContentBlock] = []
                if isinstance(raw_content, list):
                    for c in raw_content:
                        if isinstance(c, dict):
                            content_list.append(ContentBlock(
                                block_type=ContentBlockType[str(c.get("block_type", "TEXT"))],
                                content=str(c.get("content", "")),
                                is_error=bool(c.get("is_error", False)),
                            ))
                entries.append(TranscriptEntry(
                    role=str(d.get("role", "")),
                    content=content_list,
                    timestamp=str(d.get("timestamp", "")),
                    turn_seq=int(str(d.get("turn_seq", 0))),
                ))
            except Exception:
                pass
        return entries

    def list_sessions(self) -> list[SessionMeta]:
        return list(self._index.values())

    def prune(self, max_age_days: int = 30, max_entries: int = 500) -> int:
        cutoff = (
            datetime.datetime.now(datetime.UTC)
            - datetime.timedelta(days=max_age_days)
        ).isoformat()
        before = len(self._index)
        self._index = {
            k: v for k, v in self._index.items() if v.updated_at > cutoff
        }
        if len(self._index) > max_entries:
            sorted_keys = sorted(
                self._index, key=lambda k: self._index[k].updated_at,
            )
            for k in sorted_keys[: len(self._index) - max_entries]:
                del self._index[k]
                self._transcript_path(k).unlink(missing_ok=True)
        self._flush_index()
        return before - len(self._index)
