"""
Executor tool — nvidia/nemotron-3-super-120b-a12b:free via OpenRouter.
120B Total / 12B Active LatentMoE (Mamba-2 + Transformer + MTP).
1,048,576 token context processed in O(n) linear time via Mamba-2.
Trained across 21 RL environments including Terminal-Bench and SWE-RL.
2.2x throughput vs GPT-OSS-120B class via Multi-Token Prediction layers.
Also serves as Fallback 1 in the Developer cascade.
"""

from typing import Any
from jasusi_cli.core.clients import get_client, get_model

EXECUTOR_SYSTEM_PROMPT = """You are JasusiCLI's Executor agent.
You plan and generate shell command sequences to accomplish system tasks.
Rules:
- Output ONLY valid bash. No markdown prose before or after the commands.
- For multi-step tasks, number each command block and state what it verifies.
- Always include a verification step (e.g., check exit code, grep output, ls result).
- If a command may be destructive, prefix it with: # DESTRUCTIVE — requires confirmation
- Never generate commands that delete files unless the user explicitly said to delete them.
- Prefer idempotent commands where possible."""


def run_executor(task: str, context: str = "") -> str:
    model, provider = get_model("executor")
    client = get_client(provider)
    messages: list[dict[str, Any]] = [{"role": "system", "content": EXECUTOR_SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "user", "content": f"<context>\n{context}\n</context>"})
    messages.append({"role": "user", "content": task})
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=0.0,
        max_tokens=4096,
    )
    return response.choices[0].message.content or ""
