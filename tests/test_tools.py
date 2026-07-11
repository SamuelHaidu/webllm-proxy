"""ChatGPT tool-emulation unit tests (no browser): native-channel conversion and
the text `tool_call` contract parser."""

import json

from webllm_proxy.strategies.tool_calling import (
    build_preamble,
    format_tool_result,
    native_to_openai,
    parse_tool_calls,
    tool_names,
)

ALLOWED = {"write", "bash"}


def test_native_direct_args():
    c = native_to_openai(
        [{"name": "write", "arguments": '{"path": "a.py", "content": "x"}'}], ALLOWED
    )
    assert len(c) == 1 and c[0]["function"]["name"] == "write"
    assert json.loads(c[0]["function"]["arguments"]) == {"path": "a.py", "content": "x"}


def test_native_filters_chatgpt_own_tool():
    assert native_to_openai([{"name": "web", "arguments": "top story"}], ALLOWED) == []


def test_native_tool_call_recipient_wrapper():
    c = native_to_openai(
        [
            {
                "name": "tool_call",
                "arguments": '{"name": "write", "arguments": {"path": "b.py", "content": "y"}}',
            }
        ],
        ALLOWED,
    )
    assert len(c) == 1 and c[0]["function"]["name"] == "write"
    assert json.loads(c[0]["function"]["arguments"])["path"] == "b.py"


def test_native_salvages_truncated_json():
    c = native_to_openai(
        [{"name": "write", "arguments": '{"path": "c.py", "content": "z"'}], ALLOWED
    )
    assert len(c) == 1 and json.loads(c[0]["function"]["arguments"])["path"] == "c.py"


def test_native_unknown_nonjson_dropped():
    assert native_to_openai([{"name": "python", "arguments": "print(1)"}], ALLOWED) == []


def test_native_container_exec_hijacked_to_bash():
    # Thinking model's ChatGPT sandbox call -> re-routed to the client bash tool.
    c = native_to_openai(
        [
            {
                "name": "container.exec",
                "arguments": "bash -lc pwd && ls -la && find . -maxdepth 2 -type f",
            }
        ],
        ALLOWED,
    )
    assert len(c) == 1 and c[0]["function"]["name"] == "bash"
    args = json.loads(c[0]["function"]["arguments"])
    assert args == {"command": "pwd && ls -la && find . -maxdepth 2 -type f"}


def test_native_container_exec_quoted_command():
    c = native_to_openai(
        [{"name": "container.exec", "arguments": "bash -lc 'python -m pytest -q'"}], ALLOWED
    )
    assert len(c) == 1 and json.loads(c[0]["function"]["arguments"]) == {
        "command": "python -m pytest -q"
    }


def test_native_container_exec_json_command():
    c = native_to_openai(
        [{"name": "container.exec", "arguments": '{"command": "echo hi"}'}], ALLOWED
    )
    assert len(c) == 1 and json.loads(c[0]["function"]["arguments"]) == {"command": "echo hi"}


def test_native_container_exec_dropped_without_shell_tool():
    # No shell tool declared -> nothing to hijack to, so it's dropped (not faked).
    assert (
        native_to_openai(
            [{"name": "container.exec", "arguments": "bash -lc ls"}], {"write", "read"}
        )
        == []
    )


def test_native_multiple_preserved():
    c = native_to_openai(
        [
            {"name": "write", "arguments": '{"path": "calc.py", "content": "..."}'},
            {"name": "bash", "arguments": '{"command": "python calc.py"}'},
        ],
        ALLOWED,
    )
    assert [x["function"]["name"] for x in c] == ["write", "bash"]


def test_tool_names():
    assert tool_names(
        [
            {"type": "function", "function": {"name": "write"}},
            {"type": "function", "function": {"name": "bash"}},
        ]
    ) == {"write", "bash"}
    assert tool_names(None) == set()


def test_parse_tool_tag_flat():
    text = (
        "<assistant>\nOn it.\n</assistant>\n"
        '<tool>{"tool_name": "write", "path": "a.py", "content": "x=1"}</tool>'
    )
    calls, leftover = parse_tool_calls(text)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "write"
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "a.py", "content": "x=1"}
    assert leftover == "On it."


def test_parse_tool_tag_unclosed():
    text = '<tool>{"tool_name": "bash", "command": "ls -la"}'
    calls, _ = parse_tool_calls(text)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "bash"
    assert json.loads(calls[0]["function"]["arguments"])["command"] == "ls -la"


def test_parse_assistant_only_is_content():
    calls, leftover = parse_tool_calls("<assistant>Here is the answer.</assistant>")
    assert calls == [] and leftover == "Here is the answer."


def test_parse_assistant_unclosed_does_not_leak_tag():
    # Model stops generating before the closing </assistant> (e.g. hit a token
    # limit) -- the literal tag must not leak into the visible reply.
    calls, leftover = parse_tool_calls("<assistant>\nAll tests passed successfully!")
    assert calls == []
    assert leftover == "All tests passed successfully!"
    assert "<assistant>" not in leftover


def test_format_tool_result_is_tool_response():
    out = format_tool_result(
        {"tool_call_id": "c1", "name": "read_file", "content": "000001 hi"}, {"c1": "read_file"}
    )
    assert out.startswith("<tool-response>") and out.rstrip().endswith("</tool-response>")
    payload = json.loads(out[len("<tool-response>") : -len("</tool-response>")].strip())
    assert payload == {"tool_name": "read_file", "ok": True, "result": "000001 hi"}


def test_parse_closed_fence():
    text = (
        "sure\n```tool_call\n"
        '{"name": "write", "arguments": {"path": "a.py", "content": "x"}}\n'
        "```\ndone"
    )
    calls, leftover = parse_tool_calls(text)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "write"
    assert json.loads(calls[0]["function"]["arguments"])["path"] == "a.py"
    assert "sure" in leftover and "done" in leftover and "```" not in leftover


def test_parse_unclosed_fence():
    text = '```tool_call\n{"name": "bash", "arguments": {"command": "ls"}}'
    calls, _ = parse_tool_calls(text)
    assert len(calls) == 1 and calls[0]["function"]["name"] == "bash"


def test_parse_no_call_is_content():
    calls, leftover = parse_tool_calls("just a normal answer")
    assert calls == [] and leftover == "just a normal answer"


def test_build_preamble():
    pre = build_preamble(
        "You are helpful.",
        [{"type": "function", "function": {"name": "write", "description": "w", "parameters": {}}}],
    )
    assert "write" in pre and "You are helpful." in pre
    assert "<tool>" in pre and "tool_name" in pre  # AgentClip tag contract
    assert build_preamble("", None) == ""
