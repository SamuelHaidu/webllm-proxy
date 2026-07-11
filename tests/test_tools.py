"""ChatGPT tool-emulation unit tests (no browser): native-channel conversion and
the text `tool_call` contract parser."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from webllm_proxy.providers.chatgpt.tools import (  # noqa: E402
    build_preamble, format_tool_result, native_to_openai, parse_tool_calls, tool_names,
)

ALLOWED = {"write", "bash"}


def test_native_direct_args():
    c = native_to_openai([{"name": "write", "arguments": '{"path": "a.py", "content": "x"}'}], ALLOWED)
    assert len(c) == 1 and c[0]["function"]["name"] == "write"
    assert json.loads(c[0]["function"]["arguments"]) == {"path": "a.py", "content": "x"}


def test_native_filters_chatgpt_own_tool():
    assert native_to_openai([{"name": "web", "arguments": "top story"}], ALLOWED) == []


def test_native_tool_call_recipient_wrapper():
    c = native_to_openai([{"name": "tool_call",
                           "arguments": '{"name": "write", "arguments": {"path": "b.py", "content": "y"}}'}], ALLOWED)
    assert len(c) == 1 and c[0]["function"]["name"] == "write"
    assert json.loads(c[0]["function"]["arguments"])["path"] == "b.py"


def test_native_salvages_truncated_json():
    c = native_to_openai([{"name": "write", "arguments": '{"path": "c.py", "content": "z"'}], ALLOWED)
    assert len(c) == 1 and json.loads(c[0]["function"]["arguments"])["path"] == "c.py"


def test_native_unknown_nonjson_dropped():
    assert native_to_openai([{"name": "python", "arguments": "print(1)"}], ALLOWED) == []


def test_native_multiple_preserved():
    c = native_to_openai([
        {"name": "write", "arguments": '{"path": "calc.py", "content": "..."}'},
        {"name": "bash", "arguments": '{"command": "python calc.py"}'},
    ], ALLOWED)
    assert [x["function"]["name"] for x in c] == ["write", "bash"]


def test_tool_names():
    assert tool_names([{"type": "function", "function": {"name": "write"}},
                       {"type": "function", "function": {"name": "bash"}}]) == {"write", "bash"}
    assert tool_names(None) == set()


def test_parse_tool_tag_flat():
    text = ('<assistant>\nOn it.\n</assistant>\n'
            '<tool>{"tool_name": "write", "path": "a.py", "content": "x=1"}</tool>')
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


def test_format_tool_result_is_tool_response():
    out = format_tool_result({"tool_call_id": "c1", "name": "read_file", "content": "000001 hi"},
                             {"c1": "read_file"})
    assert out.startswith("<tool-response>") and out.rstrip().endswith("</tool-response>")
    payload = json.loads(out[len("<tool-response>"):-len("</tool-response>")].strip())
    assert payload == {"tool_name": "read_file", "ok": True, "result": "000001 hi"}


def test_parse_closed_fence():
    text = ('sure\n```tool_call\n'
            '{"name": "write", "arguments": {"path": "a.py", "content": "x"}}\n'
            '```\ndone')
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
    pre = build_preamble("You are helpful.",
                         [{"type": "function", "function": {"name": "write", "description": "w", "parameters": {}}}])
    assert "write" in pre and "You are helpful." in pre
    assert "<tool>" in pre and "tool_name" in pre  # AgentClip tag contract
    assert build_preamble("", None) == ""


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL PASS (%d)" % len(fns))
