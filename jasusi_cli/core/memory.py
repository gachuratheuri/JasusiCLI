"""
JasusiMemory — ChromaDB-backed semantic memory + compaction engine.
Wraps jasusi_cli.memory.compaction thresholds and session persistence.
Compaction model: gemini-2.5-flash-lite via Google AI Studio.

Three-stage compaction:
  Stage 1 (soft, 4000 tokens):  silent memory flush to ChromaDB
  Stage 2 (main, 10000 tokens): strip analysis tags, preserve 4 recent, 160-char summary
  Stage 3 (deep, 50000 tokens): structured 2000-token summary → ChromaDB worm_ledger_write
"""

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MEMORY_FLUSH_TOKENS = 4000
MAIN_COMPACT_TOKENS = 10000
DEEP_COMPACT_TOKENS = 50000
PRESERVE_RECENT = 4
COMPACTION_MAX_TOKENS = 2000

COMPACTION_SYSTEM_PROMPT = """You are a compaction agent. Summarise the conversation history
into exactly these six markdown sections. Be concise but preserve all actionable detail.

## Goals
## Files Modified
## Decisions Made
## Errors Resolved
## Pending Tasks
## Current State"""


def _get_chromadb_collection(project: str) -> Any:
    """Lazy-load ChromaDB collection. Returns None if chromadb not installed."""
    try:
        import chromadb
        persist_dir = os.path.expanduser(f"~/.jasusi/memory/{project}")
        os.makedirs(persist_dir, exist_ok=True)
        client = chromadb.PersistentClient(path=persist_dir)
        return client.get_or_create_collection(
            name="jasusi_worm_ledger",
            metadata={"hnsw:space": "cosine"},
        )
    except ImportError:
        logger.debug("chromadb not installed — memory disabled")
        return None
    except Exception as e:
        logger.warning("ChromaDB init failed: %s", e)
        return None


class JasusiMemory:
    """
    Project-scoped semantic memory backed by ChromaDB.
    Provides context loading, persistence, token estimation, and compaction.
    """

    def __init__(self, project: str | None = None) -> None:
        self._project = project or "default"
        self._collection = _get_chromadb_collection(self._project)
        self._token_estimate: int = 0
        self._history: list[dict[str, str]] = []

    def estimate_token_count(self) -> int:
        """Rough token estimate based on accumulated history char count / 4."""
        total_chars = sum(len(m.get("content", "")) for m in self._history)
        self._token_estimate = total_chars // 4
        return self._token_estimate

    def load_project_context(self, query: str, n_results: int = 5) -> str:
        """Retrieve relevant context from ChromaDB for the given query."""
        if self._collection is None:
            return ""
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=n_results,
            )
            documents = results.get("documents", [[]])
            if documents and documents[0]:
                return "\n---\n".join(documents[0])
        except Exception as e:
            logger.warning("ChromaDB query failed: %s", e)
        return ""

    def persist(self, task: str, result: str, role: str) -> None:
        """Write a task+result pair to ChromaDB for future retrieval."""
        self._history.append({"role": role, "content": result})
        if self._collection is None:
            return
        try:
            import uuid
            doc_id = str(uuid.uuid4())
            self._collection.add(
                documents=[f"[{role}] {task}\n\n{result[:2000]}"],
                ids=[doc_id],
                metadatas=[{"role": role, "type": "task_result"}],
            )
        except Exception as e:
            logger.warning("ChromaDB persist failed: %s", e)

    def compact(self) -> str:
        """
        Run compaction based on current token estimate.
        Stage 1 (4K): flush to ChromaDB silently.
        Stage 2 (10K): trim history, preserve recent 4.
        Stage 3 (50K): LLM-generated structured summary → ChromaDB worm_ledger_write.
        """
        tokens = self.estimate_token_count()

        if tokens < MEMORY_FLUSH_TOKENS:
            return "[no compaction needed]"

        # Stage 1: Memory flush — persist all history to ChromaDB
        if tokens >= MEMORY_FLUSH_TOKENS:
            self._flush_to_memory()

        # Stage 2: Main compaction — trim history
        if tokens >= MAIN_COMPACT_TOKENS:
            self._main_compact()

        # Stage 3: Deep compaction — LLM summary → worm_ledger_write
        if tokens >= DEEP_COMPACT_TOKENS:
            summary = self._deep_compact()
            # worm_ledger_write=true: always write deep compaction summary to ChromaDB
            if self._collection is not None:
                try:
                    import uuid
                    self._collection.add(
                        documents=[summary],
                        ids=[str(uuid.uuid4())],
                        metadatas=[{"type": "compaction_summary"}],
                    )
                    logger.info("Deep compaction summary written to ChromaDB")
                except Exception as e:
                    logger.warning("ChromaDB compaction write failed: %s", e)
            return f"[deep compaction complete]\n{summary}"

        return "[compaction complete]"

    def _flush_to_memory(self) -> None:
        """Stage 1: Flush accumulated history to ChromaDB."""
        if self._collection is None:
            return
        try:
            import uuid
            for msg in self._history:
                self._collection.add(
                    documents=[msg.get("content", "")[:2000]],
                    ids=[str(uuid.uuid4())],
                    metadatas=[{
                        "role": msg.get("role", "unknown"),
                        "type": "memory_flush",
                    }],
                )
        except Exception as e:
            logger.warning("Memory flush failed: %s", e)

    def _main_compact(self) -> None:
        """Stage 2: Trim history to preserve_recent_messages=4."""
        if len(self._history) > PRESERVE_RECENT:
            self._history = self._history[-PRESERVE_RECENT:]

    def wipe(self) -> None:
        """Delete all documents in the project's ChromaDB collection."""
        if self._collection is None:
            return
        try:
            import chromadb
            persist_dir = os.path.expanduser(f"~/.jasusi/memory/{self._project}")
            client = chromadb.PersistentClient(path=persist_dir)
            client.delete_collection("jasusi_worm_ledger")
            self._collection = None
            self._history.clear()
        except Exception as e:
            logger.warning("ChromaDB wipe failed: %s", e)

    def _deep_compact(self) -> str:
        """Stage 3: Use gemini-2.5-flash-lite to produce structured summary."""
        history_text = "\n".join(
            f"[{m.get('role', '?')}]: {m.get('content', '')[:500]}"
            for m in self._history
        )
        try:
            from jasusi_cli.core.clients import get_client
            client = get_client("googleai")
            response = client.chat.completions.create(
                model="gemini-2.5-flash-lite",
                messages=[
                    {"role": "system", "content": COMPACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Compact this conversation:\n\n{history_text}"},
                ],
                temperature=0.0,
                max_tokens=COMPACTION_MAX_TOKENS,
            )
            summary = response.choices[0].message.content or "[empty summary]"
        except Exception as e:
            logger.warning("Deep compaction LLM call failed: %s — using fallback", e)
            summary = (
                "## Goals\n[compaction fallback — LLM unavailable]\n"
                "## Files Modified\n[unknown]\n"
                "## Decisions Made\n[unknown]\n"
                "## Errors Resolved\n[unknown]\n"
                "## Pending Tasks\n[unknown]\n"
                "## Current State\n[context was compacted]"
            )
        self._history = [{"role": "system", "content": f"[COMPACTED CONTEXT]\n{summary}"}]
        return summary
