"""Anthropic-shaped HTTP surface: databricks' `/v1/models`, `/v1/messages`
(near pass-through SSE), and `/v1/messages/count_tokens`."""

import json
import logging

from flask import Response, jsonify, request

from ..infra.logging import dump_exchange
from ..providers.databricks import config, llmproxy
from ..wire import anthropic as wire_anthropic
from .health import requires_ready

log = logging.getLogger(__name__)


def register_databricks(app, session, provider) -> None:
    @app.get("/v1/models")
    def models():
        # `_wire` tells consumers (the gateway / pi) which HTTP surface a model
        # actually needs: ENABLED_MODELS (Claude) are Anthropic-Messages-only
        # (this route); OPENAI_MODELS (Azure GPT-4.1) only work via
        # /v1/chat/completions (see openai_routes.register_databricks_openai).
        # `_reasoning` tells them the model supports extended thinking: Claude
        # does, GPT-4.1 does not. A consumer that doesn't advertise this (e.g.
        # pi) would never send a `thinking` block, so effort/reasoning is a
        # silent no-op -- the Claude id has no "think"/"reason" keyword for a
        # name-based heuristic to catch. `_max_tokens` gives the thinking budget
        # room to breathe (see CLAUDE_MAX_TOKENS).
        data = [
            {
                "type": "model",
                "id": m,
                "display_name": m,
                "_wire": "anthropic",
                "_reasoning": True,
                "_max_tokens": config.CLAUDE_MAX_TOKENS,
            }
            for m in config.ENABLED_MODELS
        ] + [
            {"type": "model", "id": m, "display_name": m, "_wire": "openai", "_reasoning": False}
            for m in config.OPENAI_MODELS
        ]
        return jsonify(
            {
                "data": data,
                "has_more": False,
                "first_id": data[0]["id"] if data else None,
                "last_id": data[-1]["id"] if data else None,
            }
        )

    @app.post("/v1/messages")
    @requires_ready(session, wire_anthropic.unavailable_error)
    def messages():
        req = request.get_json(silent=True) or {}
        body, model = llmproxy.build_llmproxy_body(req)
        log.info(
            "messages: model=%s tools=%d stream=%s -> llmproxy",
            model,
            len(body.get("tools") or []),
            body.get("stream"),
        )
        dump_exchange(
            "databricks_proxy", {"incoming": req, "forwarded": body}, enabled=config.DEBUG_DUMP
        )

        out_q = session.submit({"path": config.LLMPROXY_PATH, "body": body})

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
            return jsonify(wire_anthropic.error_response(first[1])), 502

        def gen():
            ev = first
            while ev is not None:
                if ev[0] == "data":
                    yield ev[1]
                elif ev[0] == "error":
                    yield json.dumps(wire_anthropic.error_response(ev[1]))
                    break
                elif ev[0] == "done":
                    break
                ev = out_q.get()

        return Response(
            gen(),
            status=status,
            content_type=ctype,
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/messages/count_tokens")
    @requires_ready(session, wire_anthropic.unavailable_error)
    def count_tokens():
        """Anthropic token counting -> the llmproxy `count_tokens` endpoint.
        Non-streaming: buffers the full backend response and returns it verbatim
        (expected `{"input_tokens": N}`). If the llmproxy channel doesn't support
        count_tokens, the backend's error/status is surfaced as-is."""
        req = request.get_json(silent=True) or {}
        body, model = llmproxy.build_llmproxy_body(req)
        body["stream"] = False  # count_tokens never streams
        body.pop("max_tokens", None)  # not part of the count request
        body["_llmproxy_fields"]["endpoint"] = config.ANTHROPIC_COUNT_TOKENS_ENDPOINT
        log.info("count_tokens: model=%s tools=%d -> llmproxy", model, len(body.get("tools") or []))

        out_q = session.submit({"path": config.LLMPROXY_PATH, "body": body})
        status, ctype, chunks, backend_err = 200, "application/json", [], None
        while True:
            ev = out_q.get(timeout=180)
            if ev is None:
                break
            kind = ev[0]
            if kind == "meta":
                status = ev[1].get("status", status)
                ctype = ev[1].get("content_type") or ctype
            elif kind == "data":
                chunks.append(ev[1])
            elif kind == "error":
                backend_err = ev[1]
                break
            elif kind == "done":
                break
        payload = "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)

        # Backend supports it (real Anthropic count): return verbatim.
        if not backend_err and status == 200 and '"input_tokens"' in payload:
            return Response(payload, status=200, content_type=ctype)
        # Backend rejects count_tokens (the current reality: edge 400) -> fall back
        # to a local estimate so clients that call count_tokens don't hard-fail.
        est = llmproxy.estimate_input_tokens(req)
        log.info("count_tokens: backend unsupported (status=%s) -> local estimate %d", status, est)
        return jsonify({"input_tokens": est})
