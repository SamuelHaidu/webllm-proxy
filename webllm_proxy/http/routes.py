"""The single OpenAI-shaped controller set:

  GET  /v1/models             merged across providers (ids `<provider>__<slug>`)
  POST /v1/chat/completions    routed by the model's `<provider>__` prefix
  GET  /health                 aggregated provider readiness

A provider returns either a `chat.completion` dict (non-stream) or an SSE string
iterator (stream); this layer just serializes whichever it gets.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from flask import Response, jsonify, request

from ..utils.openai import split_model

log = logging.getLogger(__name__)


def register_routes(app, providers: dict) -> None:
    @app.get("/health")
    def health():
        per = {name: {"ready": p.ready, "error": p.error} for name, p in providers.items()}
        ok = all(v["ready"] for v in per.values()) if per else False
        return jsonify({"status": "running" if ok else "initializing", "providers": per}), (
            200 if ok else 503
        )

    @app.get("/v1/models")
    def models():
        data: list[dict] = []
        for name, p in providers.items():
            if not p.ready:
                continue
            try:
                data += p.models()
            except Exception as e:
                log.warning("models() failed for %s: %s", name, e)
        return jsonify({"object": "list", "data": data})

    @app.post("/v1/chat/completions")
    def chat_completions():
        body = request.get_json(silent=True) or {}
        provider_name, slug = split_model(body.get("model"))
        if provider_name is None or provider_name not in providers:
            return (
                jsonify(
                    {
                        "error": {
                            "message": f"model must be namespaced <provider>__<slug>; "
                            f"got {body.get('model')!r}",
                            "type": "invalid_request_error",
                        }
                    }
                ),
                400,
            )
        provider = providers[provider_name]
        if not provider.ready:
            return (
                jsonify({"error": {"message": "session initializing", "type": "unavailable"}}),
                503,
            )
        forwarded = dict(body)
        forwarded["model"] = slug
        result = provider.completions(forwarded)

        if isinstance(result, dict):
            if "error" in result:
                return jsonify(result), 502 if result["error"].get(
                    "type"
                ) == "upstream_error" else 400
            return jsonify(result)
        # streaming SSE iterator
        return Response(
            _as_iterator(result),
            mimetype="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )


def _as_iterator(result) -> Iterator[str]:
    yield from result
