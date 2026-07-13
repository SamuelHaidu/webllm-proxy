"""utils.convert: OpenAI<->Anthropic request conversion + Anthropic SSE decode,
seeded from the shapes in docs/discovery/2026-07-10-databricks-llmproxy.md."""

import json

from webllm_proxy.utils import convert


def test_openai_to_anthropic_basic():
    req = {
        "model": "claude-4-5-sonnet",
        "messages": [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
        "max_tokens": 1000,
    }
    body = convert.openai_to_anthropic(req, default_max_tokens=64000)
    assert body["system"] == [{"type": "text", "text": "be terse"}]
    assert body["messages"] == [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
    assert body["max_tokens"] == 1000


def test_openai_to_anthropic_tools_and_tool_result():
    req = {
        "messages": [
            {"role": "user", "content": "read a.py"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "t1",
                        "type": "function",
                        "function": {"name": "read", "arguments": '{"path":"a.py"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "file body"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "read",
                    "description": "read",
                    "parameters": {"type": "object"},
                },
            }
        ],
    }
    body = convert.openai_to_anthropic(req, default_max_tokens=8000)
    assert body["tools"][0]["name"] == "read"
    assistant = body["messages"][1]
    assert assistant["content"][0]["type"] == "tool_use"
    assert assistant["content"][0]["input"] == {"path": "a.py"}
    tool_turn = body["messages"][2]
    assert tool_turn["role"] == "user"
    assert tool_turn["content"][0]["type"] == "tool_result"
    assert tool_turn["content"][0]["tool_use_id"] == "t1"


def test_effort_adds_thinking_budget():
    body = convert.openai_to_anthropic(
        {"messages": [{"role": "user", "content": "x"}]}, default_max_tokens=64000, effort="max"
    )
    assert body["thinking"]["type"] == "enabled"
    assert body["thinking"]["budget_tokens"] == 32768


def test_anthropic_sse_decode():
    sse = "".join(
        f"event: {t}\ndata: {json.dumps(d)}\n\n"
        for t, d in [
            ("message_start", {"type": "message_start"}),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "thinking_delta", "thinking": "hmm"},
                },
            ),
            (
                "content_block_start",
                {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}},
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {"type": "text_delta", "text": "Hello"},
                },
            ),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
            ("message_stop", {"type": "message_stop"}),
        ]
    )
    p = convert.AnthropicSSE()
    events = list(p.feed(sse)) + list(p.flush())
    assert ("reasoning", "hmm") in events
    assert ("content", "Hello") in events
    assert ("done", "stop") in events


def test_anthropic_sse_tool_use():
    sse = "".join(
        f"data: {json.dumps(d)}\n\n"
        for d in [
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "tool_use", "id": "tu1", "name": "docSearch"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"q":'},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '"x"}'},
            },
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
            {"type": "message_stop"},
        ]
    )
    p = convert.AnthropicSSE()
    events = list(p.feed(sse)) + list(p.flush())
    assert ("tool_start", {"index": 0, "id": "tu1", "name": "docSearch"}) in events
    args = "".join(v["partial_json"] for k, v in events if k == "tool_args")
    assert json.loads(args) == {"q": "x"}
    assert ("done", "tool_calls") in events
