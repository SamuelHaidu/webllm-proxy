"""Codec unit tests — pure, offline, no network, no secrets. Validates the two
wire protocols against synthetic frames modeled on the captured shapes.

Run: `python -m webllm_proxy.providers.copilot.tests.test_protocol`  or  `pytest`.
"""

from __future__ import annotations

import json

from webllm_proxy.providers.copilot.hashcash import _leading_zero_bits, solve
from webllm_proxy.providers.copilot.models import Delta, Final
from webllm_proxy.providers.copilot.protocol.base import Ack, NeedChallenge
from webllm_proxy.providers.copilot.protocol.events import EventCodec
from webllm_proxy.providers.copilot.protocol.signalr import DELIM, SignalRCodec


def test_signalr_cumulative_deltas_and_final():
    c = SignalRCodec()
    assert isinstance(c.decode("{}" + DELIM)[0], Ack)  # handshake ack

    up1 = json.dumps({"type": 1, "target": "update", "arguments": [
        {"messages": [{"author": "bot", "messageType": "Chat", "text": "Hello"}]}]}) + DELIM
    up2 = json.dumps({"type": 1, "target": "update", "arguments": [
        {"messages": [{"author": "bot", "messageType": "Chat", "text": "Hello world"}]}]}) + DELIM
    d1 = c.decode(up1)
    d2 = c.decode(up2)
    assert d1 == [Delta("Hello")]
    assert d2 == [Delta(" world")]  # cumulative -> incremental diff

    final_raw = json.dumps({"type": 2, "item": {
        "messages": [
            {"author": "user", "text": "hi"},
            {"author": "bot", "text": "Hello world",
             "sourceAttributions": [{"providerDisplayName": "Example", "seeMoreUrl": "https://e.com"}],
             "suggestedResponses": [{"text": "more?"}]},
        ],
        "throttling": {"numUserMessagesInConversation": 2, "maxNumUserMessagesInConversation": 600},
        "conversationId": "c1", "defaultChatName": "Title", "result": {"value": "Success"},
    }}) + DELIM
    out = c.decode(final_raw)
    assert len(out) == 1 and isinstance(out[0], Final)
    f = out[0]
    assert f.text == "Hello world"
    assert f.citations[0].title == "Example" and f.citations[0].url == "https://e.com"
    assert f.suggestions[0].text == "more?"
    assert f.throttling is not None
    assert f.throttling.used == 2 and f.throttling.maximum == 600
    assert f.conversation_id == "c1" and f.title == "Title"


def test_signalr_encode_send_shape():
    c = SignalRCodec()
    frame = c.encode_send("hi", conversation_id="c1", options={"tone": "Magic", "source": "officeweb"})[0]
    obj = json.loads(frame.rstrip(DELIM))
    assert obj["type"] == 4 and obj["target"] == "chat" and obj["invocationId"] == "0"
    arg = obj["arguments"][0]
    assert arg["message"]["text"] == "hi" and arg["tone"] == "Magic"
    assert arg["conversationId"] == "c1" and arg["isStartOfSession"] is True


def test_event_incremental_deltas_and_final():
    c = EventCodec()
    assert isinstance(c.decode(json.dumps({"event": "startMessage", "conversationId": "cX"}))[0], Ack)
    assert c.decode(json.dumps({"event": "appendText", "text": "Foo"})) == [Delta("Foo")]
    assert c.decode(json.dumps({"event": "appendText", "text": "bar"})) == [Delta("bar")]
    out = c.decode(json.dumps({"event": "done", "messageId": "m"}))
    assert out == [Final(text="Foobar", conversation_id="cX")]


def test_event_hashcash_challenge_roundtrip():
    c = EventCodec()
    got = c.decode(json.dumps({"event": "challenge", "method": "hashcash", "parameter": "abc:1", "id": "0.1"}))
    assert isinstance(got[0], NeedChallenge)
    reply = c.encode_challenge_response(got[0])
    assert reply is not None
    obj = json.loads(reply)
    assert obj["event"] == "challengeResponse" and obj["method"] == "hashcash"
    assert obj["token"].isdigit()


def test_event_encode_send_shape():
    c = EventCodec()
    frame = c.encode_send("hi", conversation_id="cX", options={})[0]
    obj = json.loads(frame)
    assert obj["event"] == "send" and obj["conversationId"] == "cX"
    assert obj["content"] == [{"type": "text", "text": "hi"}] and obj["mode"] == "smart"


def test_hashcash_solver_meets_difficulty():
    import hashlib
    token = solve("resource:4")
    assert _leading_zero_bits(hashlib.sha256(("resource:4" + token).encode()).digest()) >= 4


def test_m365_model_discovery_parses_manifest():
    from webllm_proxy.providers.copilot.editions.m365 import M365Edition
    manifest = {"store": {"bizchatAsAgentGpt": {"clientPreferences": {"modelSelectorMetadata": {
        "defaultModelSelectionId": "Magic",
        "availableModelSelectionOptions": [
            {"id": "Magic", "menuItemTitle": "Auto", "menuItemDescription": "Decides how long to think"},
            {"id": "Reasoning", "menuItemTitle": "Think Deeper"},
            {"type": "itemGroup", "menuItemTitle": "GPT", "itemGroup": [
                {"id": "Gpt_5_5_Chat", "menuItemTitle": "GPT 5.5 Quick"},
                {"id": "Gpt_5_5_Reasoning", "menuItemTitle": "GPT 5.5 Think Deeper"},
            ]},
        ]}}}}}
    models = {m.id: m for m in M365Edition.parse_models(manifest)}
    assert set(models) == {"Magic", "Reasoning", "Gpt_5_5_Chat", "Gpt_5_5_Reasoning"}
    assert models["Magic"].default is True
    assert models["Reasoning"].reasoning is True
    assert models["Gpt_5_5_Reasoning"].reasoning is True and models["Gpt_5_5_Reasoning"].family == "GPT"


def test_consumer_model_discovery_from_features():
    from webllm_proxy.providers.copilot.editions.consumer import ConsumerEdition
    start = {"features": ["smart-mode-default", "deep-research-nano", "copilot-beta", "x"]}
    ids = {m.id for m in ConsumerEdition.parse_models(start)}
    assert ids == {"smart", "deep-research"}


def test_map_model_accepts_raw_id_and_enum():
    from webllm_proxy.providers.copilot.editions.m365 import M365Edition
    from webllm_proxy.providers.copilot.models import Model
    e = M365Edition()
    assert e.map_model("Gpt_5_5_Reasoning") == "Gpt_5_5_Reasoning"   # raw id passthrough
    assert e.map_model(Model.THINK) == "Reasoning"                    # normalized enum
    assert e.map_model(Model.AUTO) == "Magic"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
