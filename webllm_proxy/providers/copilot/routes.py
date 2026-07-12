"""OpenAI-compatible HTTP surface for the copilot provider: `GET /v1/models`
and `POST /v1/chat/completions` (stream + non-stream). Maps the client's
`messages[]` onto one browser turn (last user message) and folds the captured
answer events into OpenAI wire shape. Modeled on `http/openai_routes.py`."""

from __future__ import annotations

import logging
import threading
import time

from flask import Response, jsonify, request

from ...domain.conversation import ChatTurn
from ...http.health import release_lock_when_done, requires_ready
from ...wire import openai as wire_openai

log = logging.getLogger(__name__)


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return wire_openai.message_text(m)
    return ""


def _is_new_conversation(messages: list[dict]) -> bool:
    return sum(1 for m in messages if m.get("role") == "user") <= 1


def _drain(out_q) -> tuple[str, str, str | None]:
    content, finish, err = "", "stop", None
    while True:
        ev = out_q.get()
        if ev is None:
            break
        kind, val = ev
        if kind == "content":
            content += val
        elif kind == "done":
            finish = val or "stop"
        elif kind == "error":
            err = val
    return content, finish, err


def register_copilot(app, session, provider) -> None:
    lock = threading.Lock()  # serialize turns (single shared browser)

    @app.get("/v1/models")
    @requires_ready(session, wire_openai.unavailable_error)
    def models():
        data = [
            {
                "id": m.id,
                "object": "model",
                "created": 0,
                "owned_by": "microsoft",
                "_title": m.title,
                "_reasoning": m.reasoning,
            }
            for m in provider.list_models()
        ]
        return jsonify({"object": "list", "data": data})

    @app.post("/v1/chat/completions")
    @requires_ready(session, wire_openai.unavailable_error)
    def chat_completions():
        body = request.get_json(silent=True) or {}
        messages = body.get("messages") or []
        stream = bool(body.get("stream"))
        req_model = wire_openai.normalize_model(body.get("model"))
        text = _last_user_text(messages)
        if not text:
            return jsonify({"error": {"message": "no user message provided"}}), 400

        cid = wire_openai.new_id()
        created = int(time.time())
        resp_model = body.get("model") or "copilot"

        lock.acquire()
        try:
            out_q = session.submit(ChatTurn(text, req_model, _is_new_conversation(messages)))
        except Exception as e:
            lock.release()
            return jsonify({"error": {"message": str(e)}}), 500

        if stream:

            def gen():
                yield wire_openai.chunk(cid, created, resp_model, {"role": "assistant"})
                while True:
                    ev = out_q.get()
                    if ev is None:
                        break
                    kind, val = ev
                    if kind == "content":
                        yield wire_openai.chunk(cid, created, resp_model, {"content": val})
                    elif kind == "reasoning":
                        yield wire_openai.chunk(
                            cid, created, resp_model, {"reasoning_content": val}
                        )
                    elif kind == "done":
                        yield wire_openai.chunk(cid, created, resp_model, {}, finish=val or "stop")
                    elif kind == "error":
                        yield wire_openai.chunk(
                            cid, created, resp_model, {"content": f"\n[error] {val}"}, finish="stop"
                        )
                yield "data: [DONE]\n\n"

            return Response(
                release_lock_when_done(lock, gen()),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            content, finish, err = _drain(out_q)
        finally:
            lock.release()
        if err and not content:
            return jsonify({"error": {"message": err, "type": "upstream_error"}}), 502
        return jsonify(
            {
                "id": cid,
                "object": "chat.completion",
                "created": created,
                "model": resp_model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content or None},
                        "finish_reason": finish,
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }
        )
