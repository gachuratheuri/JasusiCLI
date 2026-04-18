"""
JasusiCLI v3.2 Orchestrator.
Stateless router + dispatcher. No coordinator model.
Routing: ScoredRouter v3 → 6 specialist tools.
Memory: WormLedger (ChromaDB) via memory.py — unchanged.
Compaction: auto-triggered at 3 token thresholds via memory.py.
"""

import json
from jasusi_cli.core.router import route
from jasusi_cli.core.memory import JasusiMemory
from jasusi_cli.tools.system import read_file, write_file
from jasusi_cli.tools.coder import run_developer
from jasusi_cli.tools.executor import run_executor
from jasusi_cli.tools.architect import run_architect
from jasusi_cli.tools.researcher import run_researcher
from jasusi_cli.tools.reviewer import run_reviewer

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


def run_fix(filepath: str, project: str | None = None) -> str:
    memory = JasusiMemory(project=project)
    context = memory.load_project_context(query=f"fix {filepath}")
    file_content = read_file(filepath)
    task = f"Fix all bugs and issues in this file:\n\n{file_content}"
    code = run_developer(task, context=context)
    review_result = run_reviewer(code, source_role="developer")
    review_data = json.loads(review_result)
    if review_data.get("approved"):
        write_file(filepath, code)
        memory.persist(task=task, result=code, role="developer")
        return f"Fix applied and approved by Reviewer.\n{review_data['summary']}"
    else:
        memory.persist(task=task, result=review_result, role="reviewer")
        return f"Reviewer rejected fix:\n{review_result}"
