"""chatgpt SSE v1-delta parser, pinned to the real capture."""

from pathlib import Path

from webllm_proxy.providers.chatgpt.sse import StreamAccumulator, V1DeltaParser

_SAMPLE = (
    Path(__file__).resolve().parent.parent
    / "docs/discovery/samples/sse_f_conversation.redacted.txt"
)


def _run(text):
    acc = StreamAccumulator()
    return list(acc.feed(text)) + list(acc.flush())


def test_parses_real_capture_content_and_done():
    events = _run(_SAMPLE.read_text(encoding="utf-8"))
    content = "".join(v for k, v in events if k == "content")
    assert "Zephyrine" in content
    assert content.endswith("27**.")
    assert ("done", "stop") in events
    # internal / user-echo messages never surface as content
    assert "Without me repeating them" not in content


def test_reasoning_routed_separately():
    parser = V1DeltaParser()
    lines = [
        'data: {"p": "", "o": "add", "v": {"message": {"author": {"role": "assistant"},'
        ' "content": {"content_type": "thoughts", "parts": ["thinking hard"]},'
        ' "recipient": "all", "channel": null}}}',
        "data: [DONE]",
    ]
    out = []
    for ln in lines:
        out += parser.feed_line(ln)
    assert ("reasoning", "thinking hard") in out


def test_internal_recipient_ignored():
    parser = V1DeltaParser()
    ln = (
        'data: {"p": "", "o": "add", "v": {"message": {"author": {"role": "assistant"},'
        ' "content": {"content_type": "code", "text": "bash -lc ls"},'
        ' "recipient": "container.exec", "channel": null}}}'
    )
    assert parser.feed_line(ln) == []
