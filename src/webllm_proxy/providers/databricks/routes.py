"""Anthropic Messages surface for the Databricks provider (near pass-through).

  GET  /v1/models     -> the enabled model registrations (Anthropic list shape)
  POST /v1/messages   -> wrap the request in the llmproxy envelope, run it in the
                         browser, and stream the native Anthropic SSE straight
                         back to the client.
"""
import json
import logging
import uuid

from flask import Response, jsonify, request

from . import config

log = logging.getLogger(__name__)


def build_llmproxy_body(req: dict):
    """Turn an incoming Anthropic Messages request into the Databricks llmproxy
    body: keep the Anthropic fields, add the `_llmproxy_fields` routing envelope,
    and map `model` -> `model_registration` (llmproxy has no top-level model)."""
    model = (req.get("model") or config.DEFAULT_MODEL)
    body = dict(req)
    body.pop("model", None)

    sysv = body.get("system")
    if isinstance(sysv, str) and sysv:
        body["system"] = [{"type": "text", "text": sysv}]
    elif not sysv:
        # llmproxy rejects a request with no system block (empty-body 400).
        body["system"] = [{"type": "text", "text": "You are a helpful assistant."}]

    tools = body.get("tools")
    if isinstance(tools, list):
        norm = []
        for t in tools:
            if isinstance(t, dict) and "type" not in t and "name" in t:
                t = {**t, "type": "custom"}   # llmproxy tools carry an explicit type
            norm.append(t)
        body["tools"] = norm

    body.setdefault("max_tokens", 4096)
    body.setdefault("stream", True)
    body["_llmproxy_fields"] = {
        "model_registration": model,
        "endpoint": config.ANTHROPIC_ENDPOINT,
        "agent_name": config.AGENT_NAME,
        "client_id": config.CLIENT_ID,
        "trace_id": str(uuid.uuid4()),
        "call_id": str(uuid.uuid4()),
    }
    return body, model


def register(app, session, provider):

    @app.get("/v1/models")
    def models():
        data = [{"type": "model", "id": m, "display_name": m}
                for m in config.ENABLED_MODELS]
        return jsonify({"data": data, "has_more": False,
                        "first_id": data[0]["id"] if data else None,
                        "last_id": data[-1]["id"] if data else None})

    @app.post("/v1/messages")
    def messages():
        if not session.ready:
            return jsonify({"type": "error",
                            "error": {"type": "overloaded_error",
                                      "message": "session initializing"}}), 503
        req = request.get_json(silent=True) or {}
        body, model = build_llmproxy_body(req)
        log.info("messages: model=%s tools=%d stream=%s -> llmproxy",
                 model, len(body.get("tools") or []), body.get("stream"))
        if config.DEBUG_DUMP:
            try:
                import pathlib
                pathlib.Path("/tmp/databricks_proxy_last_request.json").write_text(
                    json.dumps({"incoming": req, "forwarded": body}, indent=2)[:400000])
            except Exception:
                pass

        out_q = session.submit(body)

        # Read the response metadata (status/content-type) before streaming.
        status = 200
        ctype = "text/event-stream" if body.get("stream") else "application/json"
        first = None
        while True:
            ev = out_q.get(timeout=180)
            if ev is None:
                break
            if ev[0] == "meta":
                status = ev[1].get("status", status)
                ctype = ev[1].get("content_type") or ctype
                continue
            first = ev
            break

        if first is not None and first[0] == "error":
            return jsonify({"type": "error",
                            "error": {"type": "api_error", "message": first[1]}}), 502

        def gen():
            ev = first
            while ev is not None:
                if ev[0] == "data":
                    yield ev[1]
                elif ev[0] == "error":
                    yield json.dumps({"type": "error",
                                      "error": {"type": "api_error", "message": ev[1]}})
                    break
                elif ev[0] == "done":
                    break
                ev = out_q.get()

        return Response(gen(), status=status, content_type=ctype,
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
