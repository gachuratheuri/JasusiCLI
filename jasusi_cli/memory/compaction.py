"""Python compaction module — mirrors Rust memory::compaction."""

from __future__ import annotations

import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jasusi_cli.memory.session_store import TranscriptEntry

MEMORY_FLUSH_THRESHOLD_TOKENS: int = 4_000
MAIN_COMPACTION_THRESHOLD_TOKENS: int = 10_000
DEEP_COMPACTION_THRESHOLD_TOKENS: int = 50_000
PRESERVE_RECENT: int = 4
MAX_SUMMARY_CHARS: int = 160


class CompactionStage(Enum):
    NONE = auto()
    MEMORY_FLUSH = auto()
    MAIN = auto()
    DEEP = auto()


def required_stage(total_tokens: int) -> CompactionStage:
    if total_tokens >= DEEP_COMPACTION_THRESHOLD_TOKENS:
        return CompactionStage.DEEP
    if total_tokens >= MAIN_COMPACTION_THRESHOLD_TOKENS:
        return CompactionStage.MAIN
    if total_tokens >= MEMORY_FLUSH_THRESHOLD_TOKENS:
        return CompactionStage.MEMORY_FLUSH
    return CompactionStage.NONE


def compact_main(entries: list[TranscriptEntry], summary: str) -> list[TranscriptEntry]:
    if len(entries) <= PRESERVE_RECENT:
        return list(entries)
    summary_trimmed = summary[:MAX_SUMMARY_CHARS]
    recent = entries[-PRESERVE_RECENT:]
    return [_make_summary_entry(summary_trimmed)] + list(recent)


def _make_summary_entry(summary: str) -> TranscriptEntry:
    from jasusi_cli.memory.session_store import ContentBlock, ContentBlockType, TranscriptEntry as TE

    return TE(
        role="system",
        content=[ContentBlock(
            block_type=ContentBlockType.TEXT,
            content=f"[COMPACTED CONTEXT]: {summary}",
            is_error=False,
        )],
        timestamp=datetime.datetime.now(datetime.UTC).isoformat(),
        turn_seq=0,
    )


def compact_deep_summary(entries: list[TranscriptEntry], session_id: str) -> str:
    return (
        f"# Session Summary: {session_id}\n"
        f"**Turns:** {len(entries)}\n"
        f"**Status:** Compacted at {datetime.datetime.now(datetime.UTC).isoformat()}"
    )
