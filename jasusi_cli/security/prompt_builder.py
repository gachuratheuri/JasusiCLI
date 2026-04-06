"""SystemPromptBuilder — discovers JASUSI.md files, injects with hash guard.
RULE 10: build_turn() MUST assert fnv1a_hash(static_block) == self._static_hash every call."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

JASUSI_MD_MAX_CHARS: int = 4_000
JASUSI_MD_TOTAL_MAX_CHARS: int = 12_000
FRONTIER_MODEL_NAME: str = "Claude Opus 4.6"


def fnv1a_hash(text: str) -> int:
    """FNV-1a 32-bit hash — fast, non-cryptographic, used for prompt integrity."""
    FNV_PRIME = 0x01000193
    OFFSET_BASIS = 0x811C9DC5
    h = OFFSET_BASIS
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * FNV_PRIME) & 0xFFFFFFFF
    return h


class SystemPromptBuilder:
    def __init__(
        self,
        project_root: Path,
        frontier_model: str = FRONTIER_MODEL_NAME,
        max_chars_per_file: int = JASUSI_MD_MAX_CHARS,
        max_total_chars: int = JASUSI_MD_TOTAL_MAX_CHARS,
    ) -> None:
        self._project_root = project_root
        self._frontier_model = frontier_model
        self._max_chars_per_file = max_chars_per_file
        self._max_total_chars = max_total_chars
        self._static_block = self._build_static_block()
        # RULE 10: hash computed once at construction, verified on every build_turn()
        self._static_hash = fnv1a_hash(self._static_block)

    def _build_static_block(self) -> str:
        """Immutable portion of the system prompt — model identity + capabilities."""
        return (
            f"You are Jasusi, an AI coding agent powered by {self._frontier_model}.\n"
            "You help developers write, debug, and refactor code.\n"
            "You have access to tools: bash, file_read, file_write, file_edit, "
            "glob_search, grep_search, web_fetch, web_search, agent, todo_write.\n"
            "Always write complete implementations. Never use todo!() or unimplemented!().\n"
            "Always run tests after writing code.\n"
        )

    def _discover_jasusi_md(self) -> list[Path]:
        """Walk ancestors from project_root upward, collect JASUSI.md files."""
        found: list[Path] = []
        seen_hashes: set[str] = set()
        current = self._project_root.resolve()

        while True:
            candidate = current / "JASUSI.md"
            if candidate.exists():
                try:
                    content = candidate.read_text(encoding="utf-8")
                    content_hash = hashlib.sha256(content.encode()).hexdigest()
                    if content_hash not in seen_hashes:
                        seen_hashes.add(content_hash)
                        found.append(candidate)
                except OSError:
                    pass
            parent = current.parent
            if parent == current:
                break
            current = parent

        return found

    def _load_instruction_files(self) -> str:
        """Load and sanitize JASUSI.md files with per-file and total char limits."""
        from jasusi_cli.security.injection_guard import clean as injection_clean

        files = self._discover_jasusi_md()
        sections: list[str] = []
        total_chars = 0

        for path in files:
            if total_chars >= self._max_total_chars:
                break
            try:
                raw = path.read_text(encoding="utf-8")
                # Apply injection guard before injection
                result = injection_clean(raw)
                cleaned = result.cleaned
                if result.stripped_count > 0:
                    logging.getLogger(__name__).warning(
                        "Stripped %d injection patterns from %s",
                        result.stripped_count, path,
                    )
                # Per-file character limit
                if len(cleaned) > self._max_chars_per_file:
                    cleaned = cleaned[: self._max_chars_per_file]
                remaining = self._max_total_chars - total_chars
                if len(cleaned) > remaining:
                    cleaned = cleaned[:remaining]
                sections.append(f"# Instructions from {path.name}\n{cleaned}")
                total_chars += len(cleaned)
            except OSError:
                pass

        return "\n\n".join(sections)

    def build_turn(self) -> str:
        """Build the full system prompt for a new turn.
        RULE 10: Asserts static block integrity on every call."""
        # RULE 10: Verify static block has not been mutated
        current_hash = fnv1a_hash(self._static_block)
        assert current_hash == self._static_hash, (
            f"SystemPromptBuilder static block tampered! "
            f"Expected hash {self._static_hash:#010x}, got {current_hash:#010x}"
        )

        instruction_block = self._load_instruction_files()
        if instruction_block:
            return f"{self._static_block}\n\n{instruction_block}"
        return self._static_block

    def get_static_hash(self) -> int:
        return self._static_hash
