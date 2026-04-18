"""
Architect tool — moonshotai/kimi-k2.5 via OpenRouter.
1.04T Total / 32B Active Transformer MoE. 262K context.
$0.60/M input · $3.00/M output.
Widest knowledge bandwidth in the stack. Multimodal vision input supported.
Agent Swarm: up to 100 parallel sub-agents for complex design decomposition.
"""

from typing import Any
from jasusi_cli.core.clients import get_client, get_model

ARCHITECT_SYSTEM_PROMPT = """You are JasusiCLI's Architect agent.
You reason about system design, architectural patterns, and structural trade-offs.
Rules:
- Begin every response with: DECISION: <one-line architectural recommendation>
- Follow with: RATIONALE: <2-3 sentence explanation of trade-offs considered>
- Then provide the full design detail.
- When comparing options, use a markdown table with columns: Option | Pros | Cons | Verdict
- Flag any decision with long-term maintenance risk with: ⚠️ MAINTENANCE DEBT
- Always consider: scalability, testability, reversibility, and operational complexity."""


def run_architect(task: str, context: str = "") -> str:
    model, provider = get_model("architect")
    client = get_client(provider)
    messages: list[dict[str, Any]] = [{"role": "system", "content": ARCHITECT_SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "user", "content": f"<context>\n{context}\n</context>"})
    messages.append({"role": "user", "content": task})
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=0.2,
        max_tokens=8192,
    )
    return response.choices[0].message.content or ""
