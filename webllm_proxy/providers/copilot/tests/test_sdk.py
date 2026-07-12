"""OpenAI-style SDK tests — offline, no network. A fake transport feeds canned
SignalR frames so the whole `client.chat.completions.create` path runs.

Run: `python -m webllm_proxy.providers.copilot.tests.test_sdk`  or  `pytest`.
"""

from __future__ import annotations

import asyncio
import json

from webllm_proxy.providers.copilot import AsyncCopilot, ChatCompletion, Copilot
from webllm_proxy.providers.copilot.models import Model
from webllm_proxy.providers.copilot.protocol.signalr import DELIM
from webllm_proxy.providers.copilot.sdk import _map_model, _turn
from webllm_proxy.providers.copilot.transport import Transport


class FakeTransport(Transport):
    """Returns queued frames from recv(); ignores sends."""

    def __init__(self, frames: list[str]):
        self._frames = list(frames)

    async def connect(self, url, headers=None):
        return None

    async def send(self, data):
        return None

    async def recv(self):
        if not self._frames:
            raise ConnectionError("no more frames")
        return self._frames.pop(0)

    async def close(self):
        return None


def _m365_frames(text: str = "Hello world") -> list[str]:
    ack = "{}" + DELIM
    update = (
        json.dumps(
            {
                "type": 1,
                "target": "update",
                "arguments": [
                    {"messages": [{"author": "bot", "messageType": "Chat", "text": text}]}
                ],
            }
        )
        + DELIM
    )
    final = (
        json.dumps(
            {
                "type": 2,
                "item": {
                    "messages": [{"author": "user", "text": "hi"}, {"author": "bot", "text": text}],
                    "throttling": {
                        "numUserMessagesInConversation": 1,
                        "maxNumUserMessagesInConversation": 600,
                    },
                    "conversationId": "cid",
                    "result": {"value": "Success"},
                },
            }
        )
        + DELIM
    )
    return [ack, update, final]


def _client(frames):
    return Copilot(
        edition="m365",
        api_key="x",
        conversation="cid",
        transport_factory=lambda: FakeTransport(frames),
    )


def test_sync_non_streaming():
    client = _client(_m365_frames("Hello world"))
    resp = client.chat.completions.create(
        model="think", messages=[{"role": "user", "content": "hi"}]
    )
    assert isinstance(resp, ChatCompletion)
    assert resp.choices[0].message.content == "Hello world"
    assert resp.content == "Hello world"
    assert resp.object == "chat.completion"
    assert resp.choices[0].finish_reason == "stop"
    assert resp.throttling.used == 1 and resp.throttling.maximum == 600
    client.close()


def test_sync_streaming():
    client = _client(_m365_frames("Hi there"))
    stream = client.chat.completions.create(
        model="fast", messages=[{"role": "user", "content": "hi"}], stream=True
    )
    text = "".join(c.choices[0].delta.content or "" for c in stream)
    assert text == "Hi there"
    client.close()


def test_async_non_streaming():
    async def go():
        client = AsyncCopilot(
            edition="m365",
            api_key="x",
            conversation="cid",
            transport_factory=lambda: FakeTransport(_m365_frames("yo")),
        )
        resp = await client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
        return resp.choices[0].message.content

    assert asyncio.run(go()) == "yo"


def test_models_list_offline_fallback():
    client = Copilot(edition="m365", api_key="x", conversation="cid")
    page = client.models.list()
    ids = [m.id for m in page]
    assert "Reasoning" in ids and "Magic" in ids
    assert page.data[0].id == "Magic" and page.object == "list"
    client.close()


def test_turn_and_model_mapping():
    t = _turn(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
    )
    assert t.startswith("[system] be terse") and t.endswith("c")
    assert _map_model("think") == Model.THINK
    assert _map_model("Gpt_5_5_Reasoning") == "Gpt_5_5_Reasoning"  # raw id passthrough


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
