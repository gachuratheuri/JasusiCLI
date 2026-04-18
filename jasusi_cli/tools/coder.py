"""
Developer tool — Gemini 2.5 Flash via Google AI Studio.
500 RPD free tier. 1M token context. SWE-Bench: 60.4%.
Falls back to OpenRouter chain on quota exhaustion.
"""

import time
from typing import Any

from openai import APIStatusError, RateLimitError

from jasusi_cli.core.clients import (
    _DEVELOPER_COUNTER,
    _increment_counter,
    get_client,
    get_fallback_chain,
)

DEVELOPER_SYSTEM_PROMPT = """You are JasusiCLI's Developer agent.
You write, fix, refactor, and debug code with surgical precision.
Rules:
- Output code in fenced blocks with the correct language tag.
- When fixing a file, output the COMPLETE corrected file — never partial diffs.
- State the root cause in one sentence before the fix.
- Do not explain what code does unless explicitly asked.
- If a fix requires changes across multiple files, list all affected files first."""


def run_developer(task: str, context: str = "") -> str:
    """
    Developer role — Gemini 2.5 Flash (Google AI Studio).
    500 RPD free tier. 1M token context. SWE-Bench: 60.4%.
    Falls back to OpenRouter chain on quota exhaustion.
    """
    messages: list[dict[str, Any]] = [{"role": "system", "content": DEVELOPER_SYSTEM_PROMPT}]
    if context:
        messages.append({
            "role": "user",
            "content": f"<context>\n{context}\n</context>"
        })
    messages.append({"role": "user", "content": task})

    # Primary: Gemini 2.5 Flash (Google AI Studio, 500 RPD)
    try:
        client = get_client("googleai")
        response = client.chat.completions.create(
            model="gemini-2.5-flash",
            messages=messages,  # type: ignore[arg-type]
            temperature=0.1,
            max_tokens=8192,
        )
        _increment_counter(_DEVELOPER_COUNTER)
        return response.choices[0].message.content or ""

    except (RateLimitError, APIStatusError) as primary_err:
        status = getattr(primary_err, "status_code", None)
        if status not in (429, 402, 403, 503) and \
           "quota" not in str(primary_err).lower() and \
           "rate" not in str(primary_err).lower():
            raise  # non-quota error — do not swallow

        print("[JasusiCLI] Gemini 2.5 Flash quota exhausted. "
              "Walking OpenRouter fallback chain...")

        # Fallback: walk OpenRouter chain
        fallback_chain = get_fallback_chain()
        last_error: Exception = primary_err

        for i, entry in enumerate(fallback_chain):
            model = entry["model"]
            provider = entry["provider"]
            try:
                wait = 2 ** i
                print(f"[JasusiCLI] Developer fallback {i + 1}/"
                      f"{len(fallback_chain)}: {model} "
                      f"(waiting {wait}s...)")
                time.sleep(wait)
                client = get_client(provider)
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,  # type: ignore[arg-type]
                    temperature=0.1,
                    max_tokens=8192,
                )
                print(f"[JasusiCLI] Developer: used fallback {model}")
                return response.choices[0].message.content or ""
            except (RateLimitError, APIStatusError) as e:
                last_error = e
                continue

        raise RuntimeError(
            f"Developer role: all models exhausted.\n"
            f"Primary (Gemini 2.5 Flash): quota exceeded.\n"
            f"Fallback chain: all {len(fallback_chain)} models "
            f"rate-limited.\n"
            f"Last error: {last_error}\n"
            f"Free quota resets at midnight Pacific. "
            f"500 RPD on Flash means this should be very rare."
        )
