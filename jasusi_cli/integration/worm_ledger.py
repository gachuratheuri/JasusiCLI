from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# RULE 4: guard ChromaDB import
try:
    import chromadb
    _CHROMADB_AVAILABLE = True
except ImportError:
    _CHROMADB_AVAILABLE = False

# RULE 9: patterns to scrub before storing
_SANITISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9\-_]{20,}", re.IGNORECASE),
    re.compile(r"AIza[A-Za-z0-9\-_]{35}", re.IGNORECASE),
    re.compile(r"GROQ_[A-Za-z0-9\-_]{20,}", re.IGNORECASE),
    re.compile(r"Bearer\s+[A-Za-z0-9\-_.]{20,}", re.IGNORECASE),
    re.compile(r"eyJ[A-Za-z0-9\-_.]{40,}"),  # JWT
]


def _sanitise(text: str) -> str:
    for pattern in _SANITISE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


@dataclass
class MemoryEntry:
    doc_id: str
    text: str
    session_id: str
    tags: list[str]


class WormLedger:
    """
    Semantic memory store.
    Uses ChromaDB PersistentClient when available; falls back to in-memory list.
    API is identical in both modes so callers never need to branch.
    """

    COLLECTION_NAME: str = "jasusi_memory"

    def __init__(self, persist_dir: str = ".jasusi/memory") -> None:
        self._persist_dir = persist_dir
        self._collection: Any | None = None
        self._fallback: list[MemoryEntry] = []
        self._use_chromadb = False
        self._init_store()

    def _init_store(self) -> None:
        if not _CHROMADB_AVAILABLE:
            logger.info(
                "WormLedger: ChromaDB not available — using in-memory fallback",
            )
            return
        try:
            client: Any = chromadb.PersistentClient(
                path=self._persist_dir,
            )
            self._collection = client.get_or_create_collection(
                self.COLLECTION_NAME,
            )
            self._use_chromadb = True
            logger.info(
                "WormLedger: ChromaDB initialised at %s", self._persist_dir,
            )
        except Exception as exc:
            logger.warning(
                "WormLedger: ChromaDB init failed (%s) — using in-memory fallback",
                exc,
            )

    def upsert(
        self,
        text: str,
        session_id: str,
        tags: list[str] | None = None,
    ) -> str:
        clean_text = _sanitise(text)
        doc_id = hashlib.sha256(
            f"{session_id}:{clean_text}".encode(),
        ).hexdigest()[:16]
        entry = MemoryEntry(
            doc_id=doc_id,
            text=clean_text,
            session_id=session_id,
            tags=tags or [],
        )
        if self._use_chromadb and self._collection is not None:
            try:
                self._collection.upsert(
                    ids=[doc_id],
                    documents=[clean_text],
                    metadatas=[{
                        "session_id": session_id,
                        "tags": ",".join(tags or []),
                    }],
                )
                return doc_id
            except Exception as exc:
                logger.warning(
                    "WormLedger: ChromaDB upsert failed (%s) — using fallback",
                    exc,
                )
        self._fallback = [e for e in self._fallback if e.doc_id != doc_id]
        self._fallback.append(entry)
        return doc_id

    def query(self, text: str, n_results: int = 5) -> list[MemoryEntry]:
        if self._use_chromadb and self._collection is not None:
            try:
                results: Any = self._collection.query(
                    query_texts=[text],
                    n_results=min(
                        n_results, max(1, self._collection.count()),
                    ),
                )
                entries: list[MemoryEntry] = []
                for i, doc_id in enumerate(results["ids"][0]):
                    doc_text: str = results["documents"][0][i]
                    meta: dict[str, Any] = results["metadatas"][0][i]
                    entries.append(MemoryEntry(
                        doc_id=doc_id,
                        text=doc_text,
                        session_id=str(meta.get("session_id", "")),
                        tags=str(meta.get("tags", "")).split(","),
                    ))
                return entries
            except Exception as exc:
                logger.warning(
                    "WormLedger: ChromaDB query failed (%s) — using fallback",
                    exc,
                )
        lower = text.lower()
        matches = [e for e in self._fallback if lower in e.text.lower()]
        return matches[:n_results]

    def delete_session(self, session_id: str) -> int:
        if self._use_chromadb and self._collection is not None:
            try:
                self._collection.delete(where={"session_id": session_id})
                return -1
            except Exception as exc:
                logger.warning(
                    "WormLedger: delete_session failed (%s)", exc,
                )
        before = len(self._fallback)
        self._fallback = [
            e for e in self._fallback if e.session_id != session_id
        ]
        return before - len(self._fallback)

    def count(self) -> int:
        if self._use_chromadb and self._collection is not None:
            try:
                result: int = self._collection.count()
                return result
            except Exception:
                pass
        return len(self._fallback)

    def flush_session_to_memory(
        self,
        session_id: str,
        decisions: list[str],
        files_modified: list[str],
        pending_work: str,
    ) -> str:
        summary_lines = [
            f"## Session: {session_id}",
            "### Decisions Made",
            *[f"- {d}" for d in decisions],
            "### Files Modified",
            *[f"- {f}" for f in files_modified],
            "### Pending Work",
            pending_work or "None",
        ]
        summary = "\n".join(summary_lines)
        return self.upsert(
            summary, session_id=session_id, tags=["compaction", "pre-flush"],
        )
