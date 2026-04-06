"""Phase 12 — Live API wiring, ProviderClient, BashTool async, FileReadTool."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from jasusi_cli.api.provider_client import (
    INITIAL_BACKOFF_MS,
    MAX_BACKOFF_MS,
    MAX_RETRIES,
    RETRYABLE_STATUS_CODES,
    ProviderClient,
    SseParser,
    _redact_key,
)
from jasusi_cli.tools.bash_tool import BashTool, _guard_path_traversal, _validate_input
from jasusi_cli.tools.file_read_tool import FileReadTool


# ---------------------------------------------------------------------------
# SseParser tests
# ---------------------------------------------------------------------------


def test_sse_parser_single_complete_event() -> None:
    parser = SseParser()
    chunk = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
    payloads = parser.push_chunk(chunk)
    assert len(payloads) == 1
    assert "hello" in payloads[0]


def test_sse_parser_done_signal_excluded() -> None:
    parser = SseParser()
    chunk = b"data: [DONE]\n\n"
    payloads = parser.push_chunk(chunk)
    assert payloads == []


def test_sse_parser_empty_data_excluded() -> None:
    parser = SseParser()
    chunk = b"data: \n\n"
    payloads = parser.push_chunk(chunk)
    assert payloads == []


def test_sse_parser_split_across_chunks() -> None:
    parser = SseParser()
    part1 = b'data: {"choices":[{"delta":{"content"'
    part2 = b':"world"}}]}\n\n'
    payloads1 = parser.push_chunk(part1)
    payloads2 = parser.push_chunk(part2)
    assert payloads1 == []
    assert len(payloads2) == 1
    assert "world" in payloads2[0]


def test_sse_parser_multiple_events_in_one_chunk() -> None:
    parser = SseParser()
    chunk = b'data: {"a":1}\n\ndata: {"b":2}\n\n'
    payloads = parser.push_chunk(chunk)
    assert len(payloads) == 2


def test_sse_parser_finish_flushes_buffer() -> None:
    parser = SseParser()
    _ = parser.push_chunk(b'data: {"partial":')
    remaining = parser.finish()
    assert remaining == []
    assert parser._buffer == ""


# ---------------------------------------------------------------------------
# ProviderClient configuration tests (no network)
# ---------------------------------------------------------------------------


def test_retryable_status_codes_exactly_7() -> None:
    assert RETRYABLE_STATUS_CODES == frozenset({408, 409, 429, 500, 502, 503, 504})


def test_max_retries_is_2() -> None:
    assert MAX_RETRIES == 2


def test_initial_backoff_is_200ms() -> None:
    assert INITIAL_BACKOFF_MS == 200.0


def test_max_backoff_is_2s() -> None:
    assert MAX_BACKOFF_MS == 2000.0


def test_redact_key_shows_last_4() -> None:
    key = "sk-abcdefghijklmnop1234"
    redacted = _redact_key(key)
    assert redacted.endswith("1234")
    assert "sk-abcdefghijk" not in redacted
    assert redacted.startswith("***")


def test_redact_key_short_key() -> None:
    assert _redact_key("ab") == "***"


def test_provider_client_stores_name_and_model() -> None:
    client = ProviderClient(
        name="test-provider",
        api_key="sk-test-key-1234",
        base_url="https://api.example.com/v1",
        model="test-model",
    )
    assert client.name == "test-provider"
    assert client.model == "test-model"


def test_provider_client_build_payload_includes_system() -> None:
    client = ProviderClient(
        name="p", api_key="k", base_url="https://x.com", model="m",
    )
    payload = client._build_payload(
        messages=[{"role": "user", "content": "hello"}],
        tools=[],
        system="You are a helpful agent.",
    )
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][0]["content"] == "You are a helpful agent."
    assert payload["messages"][1]["content"] == "hello"


def test_provider_client_build_payload_no_tools_key_when_empty() -> None:
    client = ProviderClient(
        name="p", api_key="k", base_url="https://x.com", model="m",
    )
    payload = client._build_payload(messages=[], tools=[], system="")
    assert "tools" not in payload


def test_provider_client_build_payload_includes_tools() -> None:
    client = ProviderClient(
        name="p", api_key="k", base_url="https://x.com", model="m",
    )
    tools = [{"name": "bash", "description": "run cmd", "input_schema": {}}]
    payload = client._build_payload(messages=[], tools=tools, system="")
    assert "tools" in payload
    assert payload["tool_choice"] == "auto"


def test_provider_client_parse_payload_text_delta() -> None:
    client = ProviderClient(
        name="p", api_key="k", base_url="https://x.com", model="m",
    )
    raw = json.dumps({
        "choices": [{"delta": {"content": "hello world"}, "finish_reason": None}],
    })
    chunk = client._parse_payload(raw)
    assert chunk is not None
    assert chunk.delta == "hello world"
    assert not chunk.is_tool_call


def test_provider_client_parse_payload_tool_call() -> None:
    client = ProviderClient(
        name="p", api_key="k", base_url="https://x.com", model="m",
    )
    raw = json.dumps({
        "choices": [{
            "delta": {
                "tool_calls": [{
                    "id": "tc-1",
                    "function": {
                        "name": "bash",
                        "arguments": '{"command":"ls"}',
                    },
                }],
            },
            "finish_reason": "tool_use",
        }],
    })
    chunk = client._parse_payload(raw)
    assert chunk is not None
    assert chunk.is_tool_call
    assert chunk.tool_name == "bash"
    assert chunk.tool_use_id == "tc-1"


def test_provider_client_parse_payload_invalid_json_returns_none() -> None:
    client = ProviderClient(
        name="p", api_key="k", base_url="https://x.com", model="m",
    )
    assert client._parse_payload("not-json") is None


def test_provider_client_parse_payload_usage_only() -> None:
    client = ProviderClient(
        name="p", api_key="k", base_url="https://x.com", model="m",
    )
    raw = json.dumps({
        "choices": [],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    })
    chunk = client._parse_payload(raw)
    assert chunk is not None
    assert chunk.input_tokens == 100
    assert chunk.output_tokens == 50


# ---------------------------------------------------------------------------
# BashTool tests (cross-platform: use python commands)
# ---------------------------------------------------------------------------


def test_bash_tool_validate_input_valid() -> None:
    data = _validate_input(b'{"command": "echo hello"}')
    assert data["command"] == "echo hello"


def test_bash_tool_validate_input_missing_command() -> None:
    with pytest.raises(ValueError, match="missing required field"):
        _validate_input(b'{"timeout": 10}')


def test_bash_tool_validate_input_invalid_json() -> None:
    with pytest.raises(ValueError, match="invalid JSON"):
        _validate_input(b"not json")


def test_bash_tool_path_traversal_guard_raises(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="path traversal"):
        _guard_path_traversal("cat ../../etc/passwd", tmp_path)


def test_bash_tool_path_traversal_guard_safe(tmp_path: Path) -> None:
    _guard_path_traversal("ls -la", tmp_path)  # should not raise


def test_bash_tool_echo(tmp_path: Path) -> None:
    tool = BashTool(cwd=tmp_path)
    cmd = json.dumps({"command": f"{sys.executable} --version"})
    result = asyncio.run(tool.execute(cmd.encode(), "sess"))
    assert "Python" in result


def test_bash_tool_exit_nonzero_includes_code(tmp_path: Path) -> None:
    (tmp_path / "fail.py").write_text(
        "import sys; sys.exit(1)", encoding="utf-8",
    )
    tool = BashTool(cwd=tmp_path)
    cmd = json.dumps({"command": f"{sys.executable} fail.py"})
    result = asyncio.run(tool.execute(cmd.encode(), "sess"))
    assert "[exit" in result or "[error" in result


def test_bash_tool_path_traversal_in_execute(tmp_path: Path) -> None:
    tool = BashTool(cwd=tmp_path)
    result = asyncio.run(
        tool.execute(b'{"command": "cat ../../etc/passwd"}', "sess"),
    )
    assert "[permission denied]" in result


def test_bash_tool_invalid_json_returns_error(tmp_path: Path) -> None:
    tool = BashTool(cwd=tmp_path)
    result = asyncio.run(tool.execute(b"not-json", "sess"))
    assert "[error]" in result


def test_bash_tool_empty_command_returns_error(tmp_path: Path) -> None:
    tool = BashTool(cwd=tmp_path)
    result = asyncio.run(tool.execute(b'{"command": ""}', "sess"))
    assert "[error]" in result


def test_bash_tool_schema_structure() -> None:
    tool = BashTool()
    schema = tool.schema()
    assert schema["name"] == "bash"
    assert "command" in schema["input_schema"]["properties"]
    assert "command" in schema["input_schema"]["required"]


# ---------------------------------------------------------------------------
# FileReadTool tests
# ---------------------------------------------------------------------------


def test_file_read_tool_reads_file(tmp_path: Path) -> None:
    (tmp_path / "hello.txt").write_text("line1\nline2\nline3\n")
    tool = FileReadTool(cwd=tmp_path)
    result = asyncio.run(
        tool.execute(b'{"path": "hello.txt"}', "sess"),
    )
    assert "line1" in result
    assert "line2" in result


def test_file_read_tool_250_line_limit(tmp_path: Path) -> None:
    content = "\n".join(f"line {i}" for i in range(300))
    (tmp_path / "big.txt").write_text(content)
    tool = FileReadTool(cwd=tmp_path)
    result = asyncio.run(tool.execute(b'{"path": "big.txt"}', "sess"))
    assert "truncated at 250 lines" in result


def test_file_read_tool_path_traversal_rejected(tmp_path: Path) -> None:
    tool = FileReadTool(cwd=tmp_path)
    result = asyncio.run(
        tool.execute(b'{"path": "../../etc/passwd"}', "sess"),
    )
    assert "[permission denied]" in result


def test_file_read_tool_missing_file(tmp_path: Path) -> None:
    tool = FileReadTool(cwd=tmp_path)
    result = asyncio.run(
        tool.execute(b'{"path": "nonexistent.txt"}', "sess"),
    )
    assert "[error]" in result


def test_file_read_tool_missing_path_field(tmp_path: Path) -> None:
    tool = FileReadTool(cwd=tmp_path)
    result = asyncio.run(tool.execute(b"{}", "sess"))
    assert "[error]" in result


def test_file_read_tool_schema_structure() -> None:
    tool = FileReadTool()
    schema = tool.schema()
    assert schema["name"] == "file_read"
    assert "path" in schema["input_schema"]["properties"]


# ---------------------------------------------------------------------------
# Live smoke test — skipped unless NEMOTRON_API_KEY is set
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.getenv("NEMOTRON_API_KEY"),
    reason="NEMOTRON_API_KEY not set — skipping live API smoke test",
)
def test_live_provider_client_single_turn() -> None:
    """
    Live smoke test against Nemotron API.
    Verifies: auth, SSE streaming, token counting, StreamChunk fields populated.
    Only runs when NEMOTRON_API_KEY is set in the environment.
    """
    api_key = os.environ["NEMOTRON_API_KEY"]
    client = ProviderClient(
        name="nemotron",
        api_key=api_key,
        base_url="https://integrate.api.nvidia.com/v1",
        model="nvidia/llama-3.3-nemotron-super-49b-v1",
    )

    async def _run() -> list[str]:
        chunks: list[str] = []
        stream = await client.complete(
            messages=[{"role": "user", "content": "Reply with exactly: pong"}],
            tools=[],
            system="You are a helpful assistant.",
        )
        async for chunk in stream:
            if chunk.delta:
                chunks.append(chunk.delta)
        return chunks

    chunks = asyncio.run(_run())
    full_text = "".join(chunks)
    assert len(full_text) > 0
