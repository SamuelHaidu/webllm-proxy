"""`GET /v1/models` on databricks' Anthropic-shaped surface must tag each
model with `_wire` so downstream consumers (the gateway / pi) know which HTTP
surface it needs: `anthropic` for the native Claude channel (this route,
`/v1/messages`), `openai` for the Azure GPT-4.1 channel
(`/v1/chat/completions`, see openai_routes.register_databricks_openai).
Claude 404s on /v1/chat/completions -- this tag is how a client avoids that."""

from flask import Flask

from webllm_proxy.http.anthropic_routes import register_databricks
from webllm_proxy.providers.databricks import config


def _app(monkeypatch, enabled, openai):
    monkeypatch.setattr(config, "ENABLED_MODELS", enabled)
    monkeypatch.setattr(config, "OPENAI_MODELS", openai)
    app = Flask(__name__)
    register_databricks(app, session=None, provider=None)
    return app.test_client()


def test_models_tags_claude_anthropic_and_azure_openai(monkeypatch):
    client = _app(monkeypatch, ["claude-4-5-sonnet"], ["gpt-41-2025-04-14"])
    body = client.get("/v1/models").get_json()
    by_id = {m["id"]: m for m in body["data"]}
    assert by_id["claude-4-5-sonnet"]["_wire"] == "anthropic"
    assert by_id["gpt-41-2025-04-14"]["_wire"] == "openai"


def test_models_empty_lists(monkeypatch):
    client = _app(monkeypatch, [], [])
    body = client.get("/v1/models").get_json()
    assert body["data"] == []
    assert body["first_id"] is None
    assert body["last_id"] is None
