"""Workspace context discovery and assembly for JasusiCLI Agent."""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from jasusi_cli.security.injection_guard import clean as clean_injection

logger = logging.getLogger(__name__)

IGNORE_DIRS: frozenset[str] = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "env",
    ".env",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "target",
    "dist",
    "build",
    ".idea",
    ".vscode",
    ".claude",
    ".jasusi",
})


@dataclass
class WorkspaceContext:
    """Structured view of the local workspace provided to the autonomous agent."""

    project_root: Path
    file_tree: str = ""
    git_status: str = ""
    git_diff_stat: str = ""
    agents_md: str = ""
    instruction_files: list[str] = field(default_factory=list)
    total_files: int = 0

    def to_context_block(self) -> str:
        """Render workspace metadata as an XML-tagged structured context block."""
        parts: list[str] = ["<workspace_context>"]
        parts.append(f"<root>{self.project_root}</root>")
        parts.append(f"<total_files>{self.total_files}</total_files>")

        if self.agents_md:
            parts.append(f"<agents_guidance>\n{self.agents_md}\n</agents_guidance>")

        if self.instruction_files:
            instructions_joined = "\n---\n".join(self.instruction_files)
            parts.append(f"<project_instructions>\n{instructions_joined}\n</project_instructions>")

        if self.git_status:
            parts.append(f"<git_status>\n{self.git_status}\n</git_status>")

        if self.git_diff_stat:
            parts.append(f"<git_diff_stat>\n{self.git_diff_stat}\n</git_diff_stat>")

        if self.file_tree:
            parts.append(f"<file_tree>\n{self.file_tree}\n</file_tree>")

        parts.append("</workspace_context>")
        return "\n".join(parts)


def find_project_root(start_dir: Path | None = None) -> Path:
    """Discover project root by walking upward looking for repository markers."""
    current = (start_dir or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists() or (parent / "AGENTS.md").exists() or (parent / "settings.json").exists():
            return parent
    return current


def _build_file_tree(root: Path, max_files: int = 500) -> tuple[str, int]:
    """Build a compact relative file tree, skipping ignored directories."""
    files: list[str] = []
    count = 0

    for dirpath, dirnames, filenames in os.walk(root):
        # Filter directories in-place to prevent descending into ignored subtrees
        dirnames[:] = sorted([d for d in dirnames if d not in IGNORE_DIRS and not d.startswith(".")])
        filenames.sort()

        rel_dir = os.path.relpath(dirpath, root)
        prefix = "" if rel_dir == "." else f"{rel_dir}/"

        for fname in filenames:
            if fname.startswith("."):
                continue
            count += 1
            if count <= max_files:
                files.append(f"{prefix}{fname}")

    tree_str = "\n".join(files)
    if count > max_files:
        tree_str += f"\n... [{count - max_files} additional files truncated]"
    return tree_str, count


def _run_git_command(root: Path, args: list[str]) -> str:
    """Execute a git command with timeout and return trimmed stdout."""
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout.strip()
    except Exception as e:
        logger.debug("Git command %s failed: %s", args, e)
    return ""


def build_workspace_context(
    cwd: Path | None = None,
    max_files: int = 500,
) -> WorkspaceContext:
    """Construct a full WorkspaceContext for the current project."""
    root = find_project_root(cwd)
    file_tree, total_files = _build_file_tree(root, max_files=max_files)
    git_status = _run_git_command(root, ["status", "--porcelain"])
    git_diff_stat = _run_git_command(root, ["diff", "--stat"])

    agents_md = ""
    agents_path = root / "AGENTS.md"
    if agents_path.exists():
        try:
            raw = agents_path.read_text(encoding="utf-8", errors="replace")
            agents_md = clean_injection(raw)
        except Exception as e:
            logger.warning("Could not read AGENTS.md: %s", e)

    instruction_files: list[str] = []
    jasusi_md_path = root / "JASUSI.md"
    if jasusi_md_path.exists():
        try:
            raw = jasusi_md_path.read_text(encoding="utf-8", errors="replace")
            instruction_files.append(clean_injection(raw))
        except Exception as e:
            logger.warning("Could not read JASUSI.md: %s", e)

    return WorkspaceContext(
        project_root=root,
        file_tree=file_tree,
        git_status=git_status,
        git_diff_stat=git_diff_stat,
        agents_md=agents_md,
        instruction_files=instruction_files,
        total_files=total_files,
    )
