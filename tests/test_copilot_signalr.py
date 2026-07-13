"""copilot SignalR ChatHub decode: cumulative bot text -> incremental deltas."""

import json

from webllm_proxy.providers.copilot.signalr import DELIM, SignalRParse


def _frame(obj):
    return json.dumps(obj) + DELIM


def test_incremental_deltas_and_done():
    p = SignalRParse()
    frames = (
        _frame({"type": 1, "arguments": [{"messages": [{"author": "bot", "text": "Hel"}]}]})
        + _frame({"type": 1, "arguments": [{"messages": [{"author": "bot", "text": "Hello"}]}]})
        + _frame({"type": 2, "item": {}})
    )
    events = list(p.feed(frames)) + list(p.flush())
    content = "".join(v for k, v in events if k == "content")
    assert content == "Hello"
    assert ("done", "stop") in events


def test_progress_frames_ignored():
    p = SignalRParse()
    frames = _frame(
        {
            "type": 1,
            "arguments": [
                {"messages": [{"author": "bot", "messageType": "Progress", "text": "searching"}]}
            ],
        }
    )
    events = list(p.feed(frames))
    assert all(k != "content" for k, _ in events)


def test_split_frame_across_feeds():
    p = SignalRParse()
    whole = _frame({"type": 1, "arguments": [{"messages": [{"author": "bot", "text": "hi"}]}]})
    mid = len(whole) // 2
    events = list(p.feed(whole[:mid])) + list(p.feed(whole[mid:]))
    assert ("content", "hi") in events
