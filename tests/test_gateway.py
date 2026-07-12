"""Aggregator gateway unit tests (no browser, no network): pure model
namespacing/merge, upstream parsing, and the Flask surface with a fake `_http`
layer so nothing actually hits a socket."""

import json
import urllib.error

import pytest

from webllm_proxy.gateway import _http, router
from webllm_proxy.gateway.app import build_gateway_app
from webllm_proxy.gateway.upstreams import Upstream, default_upstreams, parse_upstreams


# ---- pure router ---------------------------------------------------------
def test_split_and_join_roundtrip():
    assert router.join_model("chatgpt", "gpt-5") == "chatgpt__gpt-5"
    assert router.split_model("chatgpt__gpt-5") == ("chatgpt", "gpt-5")


def test_split_model_only_first_delimiter_splits():
    assert router.split_model("databricks__a__b") == ("databricks", "a__b")


def test_split_model_unnamespaced_and_empty():
    assert router.split_model("gpt-5") == (None, "gpt-5")
    assert router.split_model("") == (None, None)
    assert router.split_model(None) == (None, None)


def test_merge_models_namespaces_sorts_and_drops_idless():
    merged = router.merge_models(
        {
            "copilot": [{"id": "think"}],
            "chatgpt": [{"id": "gpt-5", "_title": "GPT-5"}, {"id": None}],
        }
    )
    ids = [m["id"] for m in merged["data"]]
    assert ids == ["chatgpt__gpt-5", "copilot__think"]  # sorted by provider; None dropped
    assert merged["data"][0]["_provider"] == "chatgpt"
    assert merged["data"][0]["_title"] == "GPT-5"


def test_denamespace_body():
    provider, body = router.denamespace_body({"model": "databricks__claude", "messages": []})
    assert provider == "databricks" and body["model"] == "claude"
    assert router.denamespace_body({"model": "bare"})[0] is None


# ---- upstreams -----------------------------------------------------------
def test_parse_upstreams_strips_trailing_slash():
    ups = parse_upstreams("chatgpt=http://h:1/,databricks=http://h:2")
    assert ups["chatgpt"].base_url == "http://h:1"
    assert ups["databricks"].base_url == "http://h:2"


def test_default_upstreams_three_providers():
    ups = default_upstreams()
    assert set(ups) == {"chatgpt", "databricks", "copilot"}
    assert ups["chatgpt"].base_url.endswith(":5102")


# ---- Flask surface with a fake _http -------------------------------------
class _FakeResp:
    def __init__(self, lines, status=200, content_type="text/event-stream"):
        self._lines = [x if isinstance(x, bytes) else x.encode() for x in lines]
        self.status = status
        self._ct = content_type

    def __iter__(self):
        return iter(self._lines)

    def close(self):
        pass

    @property
    def headers(self):
        return {"Content-Type": self._ct}


@pytest.fixture
def ups():
    return {
        "chatgpt": Upstream("chatgpt", "http://up/chatgpt"),
        "databricks": Upstream("databricks", "http://up/databricks"),
    }


def test_models_merges_upstreams(monkeypatch, ups):
    def fake_get_json(url, timeout=10.0):
        if url.startswith("http://up/chatgpt"):
            return 200, {"data": [{"id": "gpt-5"}]}
        return 200, {"data": [{"id": "dbrx-claude"}]}

    monkeypatch.setattr(_http, "get_json", fake_get_json)
    client = build_gateway_app(ups).test_client()
    ids = {m["id"] for m in client.get("/v1/models").get_json()["data"]}
    assert ids == {"chatgpt__gpt-5", "databricks__dbrx-claude"}


def test_models_omits_unreachable_upstream(monkeypatch, ups):
    def fake_get_json(url, timeout=10.0):
        if "chatgpt" in url:
            return 200, {"data": [{"id": "gpt-5"}]}
        raise urllib.error.URLError("down")

    monkeypatch.setattr(_http, "get_json", fake_get_json)
    client = build_gateway_app(ups).test_client()
    ids = {m["id"] for m in client.get("/v1/models").get_json()["data"]}
    assert ids == {"chatgpt__gpt-5"}


def test_chat_routes_and_denamespaces(monkeypatch, ups):
    captured = {}

    def fake_open_forward(method, url, *, data=None, headers=None, timeout=300.0):
        captured["url"] = url
        captured["body"] = json.loads(data)
        return _FakeResp(['data: {"x":1}\n\n', "data: [DONE]\n\n"])

    monkeypatch.setattr(_http, "open_forward", fake_open_forward)
    client = build_gateway_app(ups).test_client()
    r = client.post("/v1/chat/completions", json={"model": "databricks__claude-x", "messages": []})
    assert r.status_code == 200
    assert captured["url"] == "http://up/databricks/v1/chat/completions"
    assert captured["body"]["model"] == "claude-x"  # de-namespaced
    assert b"[DONE]" in r.get_data()


def test_chat_rejects_unnamespaced_model(ups):
    client = build_gateway_app(ups).test_client()
    r = client.post("/v1/chat/completions", json={"model": "gpt-5", "messages": []})
    assert r.status_code == 400


def test_chat_rejects_unknown_provider(ups):
    client = build_gateway_app(ups).test_client()
    r = client.post("/v1/chat/completions", json={"model": "nope__x"})
    assert r.status_code == 400


def test_chat_502_when_upstream_unreachable(monkeypatch, ups):
    def boom(*a, **k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(_http, "open_forward", boom)
    client = build_gateway_app(ups).test_client()
    r = client.post("/v1/chat/completions", json={"model": "chatgpt__gpt-5"})
    assert r.status_code == 502


def test_health_aggregates(monkeypatch, ups):
    def fake_get_json(url, timeout=10.0):
        if "chatgpt" in url:
            return 200, {"ready": True, "status": "running"}
        raise urllib.error.URLError("down")

    monkeypatch.setattr(_http, "get_json", fake_get_json)
    client = build_gateway_app(ups).test_client()
    body = client.get("/health").get_json()
    assert body["ready"] is True
    assert body["upstreams"]["chatgpt"]["ready"] is True
    assert body["upstreams"]["databricks"]["status"] == "unreachable"
