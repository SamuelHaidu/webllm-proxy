"""The aggregator gateway Flask app: one OpenAI/Anthropic surface fronting every
running per-provider proxy. Merges their `/v1/models` (namespacing ids
`<provider>__<slug>`) and routes each request to the matching upstream by that
prefix. Holds no browser and no credentials -- it only forwards; bytes pass
through untouched and are never logged."""

from __future__ import annotations

import json
import urllib.error

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from . import _http, router
from .upstreams import Upstream, default_upstreams

_STREAM_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def build_gateway_app(upstreams: dict[str, Upstream] | None = None) -> Flask:
    ups = upstreams if upstreams is not None else default_upstreams()
    app = Flask(__name__)
    CORS(app)

    @app.get("/health")
    def health():
        return jsonify(_aggregate_health(ups))

    @app.get("/v1/models")
    def models():
        return jsonify(_collect_models(ups))

    @app.post("/v1/chat/completions")
    def chat_completions():
        return _forward_by_model(ups, "/v1/chat/completions")

    @app.post("/v1/messages")
    def messages():
        return _forward_by_model(ups, "/v1/messages")

    # Research is a chatgpt-only capability; forward the whole /v1/research API.
    @app.post("/v1/research")
    def research_create():
        return _forward_to(ups, "chatgpt", "/v1/research")

    @app.get("/v1/research")
    def research_list():
        return _forward_to(ups, "chatgpt", "/v1/research")

    @app.get("/v1/research/<job_id>")
    def research_get(job_id):
        return _forward_to(ups, "chatgpt", f"/v1/research/{job_id}")

    @app.delete("/v1/research/<job_id>")
    def research_delete(job_id):
        return _forward_to(ups, "chatgpt", f"/v1/research/{job_id}")

    return app


def _collect_models(ups: dict[str, Upstream]) -> dict:
    per: dict[str, list[dict]] = {}
    for name, up in ups.items():
        try:
            status, obj = _http.get_json(f"{up.base_url}/v1/models")
        except urllib.error.URLError:
            continue  # upstream down -> omit its models
        if status == 200 and isinstance(obj, dict):
            per[name] = obj.get("data") or []
    return router.merge_models(per)


def _aggregate_health(ups: dict[str, Upstream]) -> dict:
    upstreams: dict[str, dict] = {}
    any_ready = False
    for name, up in ups.items():
        try:
            _status, obj = _http.get_json(f"{up.base_url}/health", timeout=5.0)
            obj = obj if isinstance(obj, dict) else {}
            ready = bool(obj.get("ready"))
            any_ready = any_ready or ready
            upstreams[name] = {"url": up.base_url, "ready": ready, "status": obj.get("status")}
        except urllib.error.URLError:
            upstreams[name] = {"url": up.base_url, "ready": False, "status": "unreachable"}
    return {"gateway": "ok", "ready": any_ready, "upstreams": upstreams}


def _bad_model(ups: dict[str, Upstream], name: str | None = None):
    providers = ", ".join(sorted(ups))
    msg = (
        f"unknown provider {name!r}"
        if name
        else "model must be namespaced as '<provider>__<model>'"
    )
    body = {"error": {"message": f"{msg} (providers: {providers})", "type": "invalid_request"}}
    return jsonify(body), 400


def _forward_by_model(ups: dict[str, Upstream], path: str):
    body = request.get_json(silent=True) or {}
    provider, new_body = router.denamespace_body(body)
    if provider is None:
        return _bad_model(ups)
    up = ups.get(provider)
    if up is None:
        return _bad_model(ups, provider)
    return _stream(
        up.base_url + path,
        "POST",
        json.dumps(new_body).encode(),
        {"Content-Type": "application/json", "Accept": "text/event-stream"},
    )


def _forward_to(ups: dict[str, Upstream], provider: str, path: str):
    up = ups.get(provider)
    if up is None:
        body = {
            "error": {
                "message": f"the {provider} provider is not configured",
                "type": "invalid_request",
            }
        }
        return jsonify(body), 404
    return _stream(
        up.base_url + path,
        request.method,
        request.get_data() or None,
        {"Content-Type": request.content_type or "application/json"},
    )


def _stream(url: str, method: str, data: bytes | None, headers: dict):
    try:
        resp = _http.open_forward(method, url, data=data, headers=headers)
    except urllib.error.URLError as e:
        body = {"error": {"message": f"upstream unreachable: {e}", "type": "upstream_error"}}
        return jsonify(body), 502
    return Response(
        _http.iter_response(resp),
        status=_http.status_of(resp),
        content_type=_http.content_type_of(resp),
        headers=_STREAM_HEADERS,
    )
