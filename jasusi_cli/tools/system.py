"""
System tools — file I/O, bash, directory listing.
Preserved as-is per project rules. Used by orchestrator.py.
"""

import subprocess
import os
from pathlib import Path


def read_file(filepath: str) -> str:
    """Read and return the contents of a file."""
    p = Path(filepath)
    if not p.exists():
        return f"[error] File not found: {filepath}"
    if not p.is_file():
        return f"[error] Not a file: {filepath}"
    try:
        return p.read_text(encoding="utf-8")
    except Exception as e:
        return f"[error] Could not read {filepath}: {e}"


def write_file(filepath: str, content: str) -> str:
    """Write content to a file, creating parent directories as needed."""
    p = Path(filepath)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"[ok] Written {len(content)} chars to {filepath}"
    except Exception as e:
        return f"[error] Could not write {filepath}: {e}"


def bash(command: str, timeout: int = 30) -> str:
    """Execute a shell command and return stdout+stderr."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=os.getcwd(),
        )
        output = result.stdout
        if result.stderr:
            output += "\n" + result.stderr
        return output.strip() or f"[ok] exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return f"[error] Command timed out after {timeout}s: {command}"
    except Exception as e:
        return f"[error] Command failed: {e}"


def list_dir(dirpath: str = ".") -> str:
    """List directory contents."""
    p = Path(dirpath)
    if not p.exists():
        return f"[error] Directory not found: {dirpath}"
    if not p.is_dir():
        return f"[error] Not a directory: {dirpath}"
    try:
        entries = sorted(p.iterdir())
        lines = []
        for entry in entries[:100]:
            kind = "d" if entry.is_dir() else "f"
            lines.append(f"  [{kind}] {entry.name}")
        return "\n".join(lines) if lines else "[empty directory]"
    except Exception as e:
        return f"[error] Could not list {dirpath}: {e}"
