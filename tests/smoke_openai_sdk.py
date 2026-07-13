"""OpenAI-SDK smoke/compat suite (integration): proves the proxy is SDK-compatible
end to end, against a live running server. Skipped unless `WEBLLM_PROXY_SMOKE=1`.

Model selection (in priority order):
  1. WEBLLM_PROXY_MODELS  — comma-separated namespaced ids, tested one-per-param
  2. WEBLLM_PROXY_MODEL   — a single id (back-compat)
  3. auto-discover        — GET {base}/v1/models, one non-research model per provider

Run a real server first (`webllm-proxy serve --config-file webllm-proxy.yaml`),
then e.g.:

    WEBLLM_PROXY_SMOKE=1 \
    WEBLLM_PROXY_BASE_URL=http://127.0.0.1:5100/v1 \
    WEBLLM_PROXY_MODELS=chatgpt__gpt-5-mini,databricks__claude-4-5-sonnet \
    uv run pytest tests/smoke_openai_sdk.py -v

Usually driven by `scripts/e2e_live.py`, which boots the server, discovers the
models, and passes them in. The SDK (openai) is used ONLY here as a validation
client -- never in the runtime request path.
"""

import json
import os
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("WEBLLM_PROXY_SMOKE"),
    reason="set WEBLLM_PROXY_SMOKE=1 (+ base url/models) to run the live smoke suite",
)

BASE_URL = os.environ.get("WEBLLM_PROXY_BASE_URL", "http://127.0.0.1:5100/v1")


def _discover_models() -> list[str]:
    explicit = os.environ.get("WEBLLM_PROXY_MODELS")
    if explicit:
        return [m.strip() for m in explicit.split(",") if m.strip()]
    single = os.environ.get("WEBLLM_PROXY_MODEL")
    if single:
        return [single]
    if not os.environ.get("WEBLLM_PROXY_SMOKE"):
        return []
    try:
        with urllib.request.urlopen(BASE_URL.rstrip("/") + "/models", timeout=10) as r:
            data = json.loads(r.read())
    except Exception:
        return []
    per_provider: dict[str, str] = {}
    for m in data.get("data", []):
        mid = m.get("id", "")
        provider, sep, slug = mid.partition("__")
        if not sep or slug == "research":
            continue
        per_provider.setdefault(provider, mid)
    return list(per_provider.values())


MODELS = _discover_models()

_WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
}


@pytest.fixture
def client():
    from openai import OpenAI

    return OpenAI(base_url=BASE_URL, api_key="not-needed")


def test_discovery_found_models():
    """Guard: with SMOKE set we must have at least one model to exercise, so an
    empty parametrization can't silently pass the whole suite."""
    assert MODELS, "no models to test (set WEBLLM_PROXY_MODELS or check /v1/models)"


def test_list_models(client):
    ids = [m.id for m in client.models.list().data]
    assert ids, "no models returned"
    assert any("__" in i for i in ids), ids
    for want in MODELS:
        assert want in ids, f"{want} not in {ids}"


@pytest.mark.parametrize("model", MODELS)
def test_simple_message(client, model):
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Reply with exactly the single word: pong"}],
    )
    assert resp.choices[0].message.content


@pytest.mark.parametrize("model", MODELS)
def test_streaming_message(client, model):
    stream = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Count from one to three."}],
        stream=True,
    )
    text = "".join((c.choices[0].delta.content or "") for c in stream if c.choices)
    assert text.strip()


@pytest.mark.parametrize("model", MODELS)
def test_tool_call_round_trip(client, model):
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Use the get_weather tool for Paris."}],
        tools=[_WEATHER_TOOL],
        tool_choice="required",
    )
    calls = resp.choices[0].message.tool_calls
    assert calls, f"no tool_calls returned by {model}"
    assert calls[0].function.name == "get_weather"
    args = json.loads(calls[0].function.arguments or "{}")
    assert "city" in args


@pytest.mark.parametrize("model", MODELS)
def test_multi_turn_tool_round_trip(client, model):
    """First get a tool call, feed the result back, expect a final text answer."""
    first = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "Use the get_weather tool for Paris."}],
        tools=[_WEATHER_TOOL],
        tool_choice="required",
    )
    calls = first.choices[0].message.tool_calls
    assert calls, f"no tool_calls from {model}"
    call = calls[0]
    second = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "Use the get_weather tool for Paris."},
            {
                "role": "assistant",
                "content": first.choices[0].message.content or "",
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {
                            "name": call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": call.id,
                "content": "It is 21C and sunny in Paris.",
            },
        ],
        tools=[_WEATHER_TOOL],
    )
    msg = second.choices[0].message
    # A completed round trip yields a final assistant message (text, not another
    # forced call). Some models may re-call; accept either, just require a message.
    assert msg.content or msg.tool_calls


@pytest.mark.parametrize("model", MODELS)
def test_conversation_continuation(client, model):
    """Two sequential calls where the 2nd carries the 1st's history + a new turn
    (exercises chatgpt's stateful ConversationPlanner continuation)."""
    first = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "My favorite number is 27. Acknowledge it briefly."}],
    )
    reply = first.choices[0].message.content
    assert reply
    second = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "user", "content": "My favorite number is 27. Acknowledge it briefly."},
            {"role": "assistant", "content": reply},
            {"role": "user", "content": "What number did I say? Answer with just the number."},
        ],
    )
    answer = second.choices[0].message.content or ""
    assert "27" in answer, f"continuation lost context: {answer!r}"


@pytest.mark.parametrize("model", MODELS)
def test_reasoning_effort(client, model):
    """reasoning_effort must not break the request; note if reasoning_content came."""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "What is 17 * 23? Reason it out, then answer."}],
        reasoning_effort="high",
    )
    assert resp.choices[0].message.content
