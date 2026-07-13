"""utils.tags: the emulated tag tool-calling contract (build + parse)."""

import json

from webllm_proxy.utils import tags

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }
]


def test_build_preamble_injects_tool_names_and_schema():
    pre = tags.build_preamble("You are helpful.", _TOOLS)
    assert "You are helpful." in pre
    assert "read_file" in pre
    assert "## Available tools" in pre
    assert "Read a file" in pre


def test_build_preamble_empty_when_no_sys_no_tools():
    assert tags.build_preamble("", None) == ""


def test_build_preamble_no_unconditional_wrapper():
    """build_preamble no longer unconditionally injects a fixed wrapper --
    only what's passed in as `system_text` (the resolved, config-gated
    prompt) shows up."""
    pre = tags.build_preamble("You are helpful.", None)
    assert pre == "You are helpful."
    assert "SYSTEM INSTRUCTIONS" not in pre


def test_build_preamble_default_is_exclusive_chatgpt_wording():
    pre = tags.build_preamble("", _TOOLS)
    assert "the only actions available to you" in pre
    assert "custom tool(s)" not in pre


def test_build_preamble_non_exclusive_softens_the_claim():
    """copilot passes exclusive=False (its model resists an absolute "only these
    tools exist" claim since it has real server-side tools of its own)."""
    pre = tags.build_preamble("", _TOOLS, exclusive=False)
    assert "the only actions available to you" not in pre
    assert "custom tool(s)" in pre
    assert "read_file" in pre  # tool injection itself is unaffected


def test_build_preamble_contract_prompt_selects_file():
    pre = tags.build_preamble("", _TOOLS, contract_prompt="webui_tool_contract_copilot")
    assert "external harness" in pre


def test_parse_closed_tool_tag():
    text = (
        '<assistant>reading now</assistant><tool>{"tool_name": "read_file", "path": "a.py"}</tool>'
    )
    calls, leftover = tags.parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "read_file"
    assert json.loads(calls[0]["function"]["arguments"]) == {"path": "a.py"}
    assert leftover == "reading now"


def test_parse_unclosed_tool_tag():
    text = '<tool>{"tool_name": "read_file", "path": "big.py"'
    calls, _ = tags.parse_tool_calls(text)
    assert len(calls) == 1
    assert calls[0]["function"]["name"] == "read_file"


def test_parse_plain_assistant_text():
    calls, leftover = tags.parse_tool_calls("<assistant>just an answer</assistant>")
    assert calls == []
    assert leftover == "just an answer"


def test_parse_no_tags_returns_text():
    calls, leftover = tags.parse_tool_calls("hello world")
    assert calls == []
    assert leftover == "hello world"


def test_format_tool_result():
    out = tags.format_tool_result(
        {"role": "tool", "tool_call_id": "c1", "content": "ok"}, {"c1": "read_file"}
    )
    assert "<tool-response>" in out
    payload = json.loads(out.split(">", 1)[1].rsplit("<", 1)[0])
    assert payload["tool_name"] == "read_file"
    assert payload["result"] == "ok"
