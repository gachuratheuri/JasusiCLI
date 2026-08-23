"""
JasusiCLI v3.2 Orchestrator.
Stateless router + dispatcher. No coordinator model.
Routing: ScoredRouter v3 → 6 specialist tools.
Memory: WormLedger (ChromaDB) via memory.py — unchanged.
Compaction: auto-triggered at 3 token thresholds via memory.py.
"""

import json

from jasusi_cli.core.memory import JasusiMemory
from jasusi_cli.core.router import route
from jasusi_cli.tools.architect import run_architect
from jasusi_cli.tools.coder import run_developer
from jasusi_cli.tools.executor import run_executor
from jasusi_cli.tools.researcher import run_researcher
from jasusi_cli.tools.reviewer import run_reviewer
from jasusi_cli.tools.system import read_file

DISPATCH = {
    "developer":  run_developer,
    "executor":   run_executor,
    "architect":  run_architect,
    "researcher": run_researcher,
}


def run_task(task: str, project: str | None = None) -> str:
    memory = JasusiMemory(project=project)
    token_count = memory.estimate_token_count()
    context = memory.load_project_context(query=task)
    role = route(task, token_count=token_count)

    if role == "compaction":
        memory.compact()
        return "[Compaction complete — context compressed and written to WormLedger]"

    if role == "reviewer":
        code = run_developer(task, context=context)
        result = run_reviewer(code, source_role="developer")
        memory.persist(task=task, result=result, role=role)
        return result

    handler = DISPATCH[role]
    result = handler(task, context=context)
    memory.persist(task=task, result=result, role=role)
    return result


class ReviewerProtocolError(RuntimeError):
    """The reviewer returned something that is not a valid review verdict.

    Raised instead of letting ``JSONDecodeError``/``KeyError`` escape: reviewer
    output is untrusted model text, and malformed output is an expected
    condition of the protocol, not an internal fault.
    """


def _parse_review(review_result: str) -> tuple[bool, str]:
    """Parse a reviewer verdict into ``(approved, summary)``.

    Model output is untrusted data. Every field is validated before use.
    """
    try:
        payload = json.loads(review_result)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ReviewerProtocolError(
            "reviewer returned output that is not valid JSON",
        ) from exc

    if not isinstance(payload, dict):
        raise ReviewerProtocolError("reviewer verdict must be a JSON object")

    approved = payload.get("approved")
    if not isinstance(approved, bool):
        raise ReviewerProtocolError("reviewer verdict is missing a boolean 'approved'")

    summary = payload.get("summary")
    if not isinstance(summary, str):
        summary = "(reviewer supplied no summary)"

    return approved, summary


def run_fix(filepath: str, project: str | None = None) -> str:
    """Propose a fix for ``filepath`` and return it for review.

    This function never writes to the source file. Applying a change requires a
    validated, reversible patch transaction (``runtime::transactional_patch``);
    writing raw model output back over a source file has no applicability check,
    no syntax validation, and no rollback record.
    """
    memory = JasusiMemory(project=project)
    context = memory.load_project_context(query=f"fix {filepath}")
    file_content = read_file(filepath)
    task = f"Fix all bugs and issues in this file:\n\n{file_content}"
    code = run_developer(task, context=context)
    review_result = run_reviewer(code, source_role="developer")

    try:
        approved, summary = _parse_review(review_result)
    except ReviewerProtocolError as exc:
        memory.persist(task=task, result=review_result, role="reviewer")
        return f"Fix not proposed — {exc}.\nRaw reviewer output:\n{review_result}"

    if not approved:
        memory.persist(task=task, result=review_result, role="reviewer")
        return f"Reviewer rejected fix:\n{review_result}"

    memory.persist(task=task, result=code, role="developer")
    return (
        "[PROPOSAL ONLY — this file was not modified]\n"
        f"Reviewer approved the proposed fix:\n{summary}\n\n"
        f"Proposed Code:\n{code}"
    )
