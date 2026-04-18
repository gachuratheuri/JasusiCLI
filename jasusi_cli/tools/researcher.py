"""
Researcher tool — gemini-2.5-pro via Google AI Studio.
1,048,576 token context. 5 RPM / 100 RPD free tier.
Native thinking mode auto-activates on complex queries.
Soft cap at 90 RPD: degrades gracefully to gemini-2.5-flash.
"""

import os
from datetime import date
from typing import Any

from jasusi_cli.core.clients import get_client

RESEARCHER_SYSTEM_PROMPT = """You are JasusiCLI's Researcher agent.
You answer deep technical questions with precision and cite your reasoning.
Rules:
- Lead with the direct answer in one sentence, then elaborate.
- When explaining a concept, provide one concrete code example if applicable.
- If you are uncertain, say so explicitly — never fabricate API details or version numbers.
- For documentation lookups, state the source (library name + version) at the end."""

_RPD_COUNTER_FILE = os.path.expanduser("~/.jasusi_researcher_rpd")


def _get_today_count() -> int:
    if not os.path.exists(_RPD_COUNTER_FILE):
        return 0
    with open(_RPD_COUNTER_FILE) as f:
        data = f.read().strip().split(",")
    if len(data) == 2 and data[0] == str(date.today()):
        return int(data[1])
    return 0


def _increment_today_count() -> int:
    count = _get_today_count() + 1
    with open(_RPD_COUNTER_FILE, "w") as f:
        f.write(f"{date.today()},{count}")
    return count


def run_researcher(query: str, context: str = "") -> str:
    SOFT_CAP = 90
    count = _get_today_count()
    if count >= SOFT_CAP:
        model = "gemini-2.5-flash"
    else:
        model = "gemini-2.5-pro"
    client = get_client("googleai")
    messages: list[dict[str, Any]] = [{"role": "system", "content": RESEARCHER_SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "user", "content": f"<context>\n{context}\n</context>"})
    messages.append({"role": "user", "content": query})
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        temperature=0.3,
        max_tokens=4096,
    )
    _increment_today_count()
    return response.choices[0].message.content or ""
