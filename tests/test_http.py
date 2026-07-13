"""http.routes: model merge, prefix routing, streaming passthrough, using a
fake in-memory provider (no browser)."""

import pytest

from webllm_proxy.http import build_app


class FakeProvider:
    name = "fake"
    ready = True
    error = None

    def models(self):
        return [{"id": "fake__m1", "object": "model"}]

    def completions(self, request):
        if request.get("stream"):

            def gen():
                yield 'data: {"x":1}\n\n'
                yield "data: [DONE]\n\n"

            return gen()
        return {"id": "c1", "object": "chat.completion", "_echo_model": request["model"]}


@pytest.fixture
def client():
    app = build_app({"fake": FakeProvider()})
    app.testing = True
    return app.test_client()


def test_models_merged(client):
    r = client.get("/v1/models")
    assert r.status_code == 200
    ids = [m["id"] for m in r.get_json()["data"]]
    assert ids == ["fake__m1"]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["status"] == "running"


def test_chat_routes_by_prefix_and_strips_it(client):
    r = client.post("/v1/chat/completions", json={"model": "fake__m1", "messages": []})
    assert r.status_code == 200
    assert r.get_json()["_echo_model"] == "m1"


def test_chat_rejects_unnamespaced_model(client):
    r = client.post("/v1/chat/completions", json={"model": "m1", "messages": []})
    assert r.status_code == 400


def test_chat_unknown_provider(client):
    r = client.post("/v1/chat/completions", json={"model": "nope__m1", "messages": []})
    assert r.status_code == 400


def test_chat_stream(client):
    r = client.post(
        "/v1/chat/completions", json={"model": "fake__m1", "messages": [], "stream": True}
    )
    assert r.status_code == 200
    assert "[DONE]" in r.get_data(as_text=True)
