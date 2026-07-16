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


def test_openai_to_anthropic_always_streams_upstream():
    # Regardless of the OpenAI client's own `stream` field, the upstream
    # Anthropic body must request `stream: true` -- our capture layer only
    # understands `text/event-stream`; a non-streaming upstream response
    # comes back as buffered `application/json` and silently produces an
    # empty reply (found live). Client-side stream/non-stream is handled by
    # the provider collapsing the SSE itself, not by this flag.
    for client_stream in (False, True, None):
        req: dict = {"messages": [{"role": "user", "content": "hi"}]}
        if client_stream is not None:
            req["stream"] = client_stream
        body = convert.openai_to_anthropic(req, default_max_tokens=64000)
        assert body["stream"] is True


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


def test_thinking_budget_respects_floor_and_raises_max_tokens():
    # tiny max_tokens + high effort: the budget can neither be the full 32768
    # nor drop below Anthropic's 1024 floor, so max_tokens is raised to keep
    # max_tokens > budget_tokens valid.
    body = convert.openai_to_anthropic(
        {"messages": [{"role": "user", "content": "x"}], "max_tokens": 1000},
        default_max_tokens=64000,
        effort="max",
    )
    assert body["thinking"]["budget_tokens"] == 1024
    assert body["max_tokens"] > body["thinking"]["budget_tokens"]


def test_thinking_budget_shrinks_under_small_max_tokens_without_raising_it():
    body = convert.openai_to_anthropic(
        {"messages": [{"role": "user", "content": "x"}], "max_tokens": 4096},
        default_max_tokens=64000,
        effort="max",
    )
    # 32768 wouldn't fit under max_tokens=4096, so the budget shrinks to leave
    # the reply margin, but the client's max_tokens is left as-is.
    assert body["thinking"]["budget_tokens"] == 4096 - 512
    assert body["max_tokens"] == 4096


def test_temperature_and_top_p_dropped_when_thinking_enabled():
    body = convert.openai_to_anthropic(
        {"messages": [{"role": "user", "content": "x"}], "temperature": 0.2, "top_p": 0.9},
        default_max_tokens=64000,
        effort="max",
    )
    assert "temperature" not in body
    assert "top_p" not in body
    assert body["thinking"]["type"] == "enabled"


def test_temperature_and_top_p_forwarded_without_thinking():
    body = convert.openai_to_anthropic(
        {"messages": [{"role": "user", "content": "x"}], "temperature": 0.2, "top_p": 0.9},
        default_max_tokens=64000,
    )
    assert body["temperature"] == 0.2
    assert body["top_p"] == 0.9
    assert "thinking" not in body


def test_tool_choice_forced_without_thinking():
    req = {"messages": [], "tool_choice": "required"}
    assert convert.openai_to_anthropic(req, default_max_tokens=8000)["tool_choice"] == {
        "type": "any"
    }
    req2 = {"messages": [], "tool_choice": {"type": "function", "function": {"name": "f"}}}
    assert convert.openai_to_anthropic(req2, default_max_tokens=8000)["tool_choice"] == {
        "type": "tool",
        "name": "f",
    }


def test_tool_choice_not_forced_when_thinking_enabled():
    # forced tool use is incompatible with extended thinking -> downgraded to auto
    for tc in ("required", {"type": "function", "function": {"name": "f"}}):
        body = convert.openai_to_anthropic(
            {"messages": [], "tool_choice": tc}, default_max_tokens=64000, effort="max"
        )
        assert body["tool_choice"] == {"type": "auto"}


def test_tool_choice_none_and_auto_passthrough():
    assert convert.openai_to_anthropic(
        {"messages": [], "tool_choice": "none"}, default_max_tokens=8000
    )["tool_choice"] == {"type": "none"}
    assert convert.openai_to_anthropic(
        {"messages": [], "tool_choice": "auto"}, default_max_tokens=8000
    )["tool_choice"] == {"type": "auto"}


def test_parallel_tool_calls_false_disables_parallel():
    body = convert.openai_to_anthropic(
        {"messages": [], "parallel_tool_calls": False}, default_max_tokens=8000
    )
    assert body["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    body2 = convert.openai_to_anthropic(
        {"messages": [], "tool_choice": "required", "parallel_tool_calls": False},
        default_max_tokens=8000,
    )
    assert body2["tool_choice"] == {"type": "any", "disable_parallel_tool_use": True}


def test_stop_maps_to_stop_sequences():
    assert convert.openai_to_anthropic({"messages": [], "stop": "STOP"}, default_max_tokens=8000)[
        "stop_sequences"
    ] == ["STOP"]
    assert convert.openai_to_anthropic(
        {"messages": [], "stop": ["a", "b"]}, default_max_tokens=8000
    )["stop_sequences"] == ["a", "b"]


def test_max_completion_tokens_fallback():
    body = convert.openai_to_anthropic(
        {"messages": [], "max_completion_tokens": 1234}, default_max_tokens=8000
    )
    assert body["max_tokens"] == 1234
    # max_tokens wins when both are present
    body2 = convert.openai_to_anthropic(
        {"messages": [], "max_tokens": 10, "max_completion_tokens": 1234}, default_max_tokens=8000
    )
    assert body2["max_tokens"] == 10


def test_user_maps_to_metadata_user_id():
    body = convert.openai_to_anthropic({"messages": [], "user": "u-42"}, default_max_tokens=8000)
    assert body["metadata"] == {"user_id": "u-42"}


def test_adaptive_thinking_for_capable_model():
    body = convert.openai_to_anthropic(
        {"messages": [{"role": "user", "content": "x"}]},
        default_max_tokens=64000,
        effort="max",
        model="claude-sonnet-5",
    )
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert body["output_config"] == {"effort": "max"}
    assert "budget_tokens" not in body["thinking"]


def test_adaptive_effort_mapping():
    for rung, level in [
        ("min", "low"),
        ("standard", "medium"),
        ("extended", "high"),
        ("max", "max"),
    ]:
        body = convert.openai_to_anthropic(
            {"messages": []}, default_max_tokens=64000, effort=rung, model="claude-opus-4-8"
        )
        assert body["output_config"]["effort"] == level


def test_adaptive_vs_manual_model_matrix():
    # both databricks (claude-4-6-sonnet) and Anthropic (claude-sonnet-4-6) naming
    adaptive = [
        "claude-sonnet-5",
        "claude-sonnet-4-6",
        "claude-4-6-sonnet",
        "claude-opus-4-6",
        "claude-4-8-opus",
        "claude-mythos-preview",
    ]
    manual = [
        "claude-4-5-sonnet",
        "claude-sonnet-4-5",
        "claude-4-5-haiku",
        "claude-3-7-sonnet",
        None,
        "gpt-4.1",
    ]
    for m in adaptive:
        body = convert.openai_to_anthropic(
            {"messages": []}, default_max_tokens=64000, effort="max", model=m
        )
        assert body["thinking"]["type"] == "adaptive", m
    for m in manual:
        body = convert.openai_to_anthropic(
            {"messages": []}, default_max_tokens=64000, effort="max", model=m
        )
        assert body["thinking"]["type"] == "enabled", m


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


def test_anthropic_sse_captures_real_usage():
    sse = "".join(
        f"data: {json.dumps(d)}\n\n"
        for d in [
            {
                "type": "message_start",
                "message": {
                    "usage": {
                        "input_tokens": 1885,
                        "cache_creation_input_tokens": 9673,
                        "cache_read_input_tokens": 34483,
                    }
                },
            },
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "hi"},
            },
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn"},
                "usage": {"output_tokens": 91},
            },
            {"type": "message_stop"},
        ]
    )
    p = convert.AnthropicSSE()
    list(p.feed(sse)) + list(p.flush())
    assert p.usage == {
        "input_tokens": 1885,
        "cache_creation_input_tokens": 9673,
        "cache_read_input_tokens": 34483,
        "output_tokens": 91,
    }
    assert p.openai_usage() == {
        "prompt_tokens": 1885 + 9673 + 34483,
        "completion_tokens": 91,
        "total_tokens": 1885 + 9673 + 34483 + 91,
    }


def test_anthropic_sse_no_usage_reported():
    p = convert.AnthropicSSE()
    list(p.feed('data: {"type":"message_stop"}\n\n')) + list(p.flush())
    assert p.usage == {}
    assert p.openai_usage() is None


def test_anthropic_usage_to_openai_empty_and_partial():
    assert convert.anthropic_usage_to_openai({}) is None
    assert convert.anthropic_usage_to_openai({"input_tokens": 0, "output_tokens": 0}) is None
    assert convert.anthropic_usage_to_openai({"input_tokens": 10}) == {
        "prompt_tokens": 10,
        "completion_tokens": 0,
        "total_tokens": 10,
    }


def test_anthropic_sse_captures_signature():
    sse = "".join(
        f"data: {json.dumps(d)}\n\n"
        for d in [
            {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "thinking_delta", "thinking": "hmm"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "sig-"},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "signature_delta", "signature": "abc"},
            },
            {"type": "message_stop"},
        ]
    )
    p = convert.AnthropicSSE()
    events = list(p.feed(sse)) + list(p.flush())
    assert ("reasoning", "hmm") in events
    assert p.signatures == {0: "sig-abc"}


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
