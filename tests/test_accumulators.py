"""Accumulator unit tests (no browser): the Databricks pass-through and the
ChatGPT v1-delta parser both satisfy the core Accumulator contract."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from webllm_proxy.providers.base import PassthroughAccumulator  # noqa: E402
from webllm_proxy.providers.chatgpt.sse import StreamAccumulator  # noqa: E402


def test_passthrough_forwards_bytes():
    acc = PassthroughAccumulator()
    assert list(acc.feed("event: message_start\n")) == [("data", "event: message_start\n")]
    assert list(acc.feed("")) == []
    assert list(acc.flush()) == []
    assert acc.finish_reason is None


def test_v1_parser_emits_content():
    acc = StreamAccumulator()
    add = {"o": "add", "p": "", "v": {"message": {
        "id": "m1", "author": {"role": "assistant"}, "recipient": "all",
        "content": {"content_type": "text", "parts": [""]}}}}
    app = {"o": "append", "p": "/message/content/parts/0", "v": "Hello"}
    chunk = "data: " + json.dumps(add) + "\n" + "data: " + json.dumps(app) + "\n"
    events = list(acc.feed(chunk))
    assert ("content", "Hello") in events
    # no [DONE] in-band -> finish_reason falls back to "stop"
    assert acc.finish_reason == "stop"


def test_v1_parser_reasoning_vs_content():
    acc = StreamAccumulator()
    add = {"o": "add", "p": "", "v": {"message": {
        "id": "r1", "author": {"role": "assistant"}, "recipient": "all",
        "content": {"content_type": "thoughts", "parts": [""]}}}}
    app = {"o": "append", "p": "/message/content/parts/0", "v": "thinking..."}
    events = list(acc.feed("data: " + json.dumps(add) + "\ndata: " + json.dumps(app) + "\n"))
    assert ("reasoning", "thinking...") in events
    assert all(k != "content" for k, _ in events)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
    print("ALL PASS (%d)" % len(fns))
