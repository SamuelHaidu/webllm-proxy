"""Accumulator unit tests (no browser): the Databricks pass-through and the
ChatGPT v1-delta parser both satisfy the core Accumulator contract."""

import json

from webllm_proxy.domain.ports import PassthroughAccumulator
from webllm_proxy.providers.chatgpt.sse import StreamAccumulator


def test_passthrough_forwards_bytes():
    acc = PassthroughAccumulator()
    assert list(acc.feed("event: message_start\n")) == [("data", "event: message_start\n")]
    assert list(acc.feed("")) == []
    assert list(acc.flush()) == []
    assert acc.finish_reason is None


def test_v1_parser_emits_content():
    acc = StreamAccumulator()
    add = {
        "o": "add",
        "p": "",
        "v": {
            "message": {
                "id": "m1",
                "author": {"role": "assistant"},
                "recipient": "all",
                "content": {"content_type": "text", "parts": [""]},
            }
        },
    }
    app = {"o": "append", "p": "/message/content/parts/0", "v": "Hello"}
    chunk = "data: " + json.dumps(add) + "\n" + "data: " + json.dumps(app) + "\n"
    events = list(acc.feed(chunk))
    assert ("content", "Hello") in events
    # no [DONE] in-band -> finish_reason falls back to "stop"
    assert acc.finish_reason == "stop"


def test_v1_parser_reasoning_vs_content():
    acc = StreamAccumulator()
    add = {
        "o": "add",
        "p": "",
        "v": {
            "message": {
                "id": "r1",
                "author": {"role": "assistant"},
                "recipient": "all",
                "content": {"content_type": "thoughts", "parts": [""]},
            }
        },
    }
    app = {"o": "append", "p": "/message/content/parts/0", "v": "thinking..."}
    events = list(acc.feed("data: " + json.dumps(add) + "\ndata: " + json.dumps(app) + "\n"))
    assert ("reasoning", "thinking...") in events
    assert all(k != "content" for k, _ in events)


def test_v1_parser_captures_thoughts_as_reasoning():
    # The thinking model's chain-of-thought arrives as content.thoughts[] (each a
    # {summary, content} block), not parts -> must surface as reasoning, not content.
    acc = StreamAccumulator()
    add = {
        "o": "add",
        "p": "",
        "v": {
            "message": {
                "id": "t1",
                "author": {"role": "assistant"},
                "recipient": "all",
                "content": {
                    "content_type": "thoughts",
                    "thoughts": [
                        {"summary": "Planning", "content": "I'll write calc.py then run tests."}
                    ],
                },
            }
        },
    }
    events = list(acc.feed("data: " + json.dumps(add) + "\n"))
    reasoning = "".join(v for k, v in events if k == "reasoning")
    assert "Planning" in reasoning and "write calc.py" in reasoning
    assert all(k != "content" for k, _ in events)


def test_v1_parser_commentary_channel_is_reasoning():
    # ChatGPT's "commentary" channel is thinking narration, not the answer.
    acc = StreamAccumulator()
    add = {
        "o": "add",
        "p": "",
        "v": {
            "message": {
                "id": "cm1",
                "author": {"role": "assistant"},
                "recipient": "all",
                "channel": "commentary",
                "content": {"content_type": "text", "parts": [""]},
            }
        },
    }
    app = {"o": "append", "p": "/message/content/parts/0", "v": "I'm adding the parser first."}
    events = list(acc.feed("data: " + json.dumps(add) + "\ndata: " + json.dumps(app) + "\n"))
    assert ("reasoning", "I'm adding the parser first.") in events
    assert all(k != "content" for k, _ in events)


def test_v1_parser_final_channel_is_content():
    # The real answer (channel "final") stays content even for a thinking model.
    acc = StreamAccumulator()
    add = {
        "o": "add",
        "p": "",
        "v": {
            "message": {
                "id": "f1",
                "author": {"role": "assistant"},
                "recipient": "all",
                "channel": "final",
                "content": {"content_type": "text", "parts": [""]},
            }
        },
    }
    app = {"o": "append", "p": "/message/content/parts/0", "v": "Done. 7 passed."}
    events = list(acc.feed("data: " + json.dumps(add) + "\ndata: " + json.dumps(app) + "\n"))
    assert ("content", "Done. 7 passed.") in events
    assert all(k != "reasoning" for k, _ in events)


def test_v1_parser_captures_container_exec_code_text():
    # A native tool call (recipient != all) delivered as a single `add` with the
    # payload in content.text (content_type "code", no parts) -- e.g. a thinking
    # model's `container.exec` sandbox call. Must be captured as a tool_call, not
    # dropped, and must NOT leak into the answer content.
    acc = StreamAccumulator()
    add = {
        "o": "add",
        "p": "",
        "v": {
            "message": {
                "id": "c1",
                "author": {"role": "assistant"},
                "recipient": "container.exec",
                "content": {"content_type": "code", "text": "bash -lc ls -la"},
            }
        },
    }
    events = list(acc.feed("data: " + json.dumps(add) + "\n")) + list(acc.flush())
    assert ("tool_call", {"name": "container.exec", "arguments": "bash -lc ls -la"}) in events
    assert all(k != "content" for k, _ in events)
