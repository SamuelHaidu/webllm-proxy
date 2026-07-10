"""OpenAI-compatible HTTP surface.

Endpoints:
  GET  /health
  GET  /v1/models              -> real ChatGPT slugs (no aliasing)
  POST /v1/chat/completions    -> stream or non-stream

Stateful: maps the stateless OpenAI `messages[]` onto ONE ongoing ChatGPT
conversation and sends only the newest user message (a fresh/diverging history
starts a new ChatGPT conversation). See docs/discovery/ for the design.
"""
import json
import logging
import threading
import time
import uuid

from flask import Flask, Response, jsonify, request
from flask_cors import CORS

from . import config
from .browser import BrowserSession

log = logging.getLogger(__name__)


def _msg_text(m) -> str:
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for part in c:
            if isinstance(part, dict) and part.get("type") == "text":
                out.append(part.get("text", ""))
            elif isinstance(part, str):
                out.append(part)
        return "\n".join(out)
    return "" if c is None else str(c)


def _norm_model(model):
    if not model:
        return None
    if model.strip().lower() in ("auto", "default", "chatgpt", "gpt", ""):
        return None
    return model.strip()


def create_app(session: BrowserSession) -> Flask:
    app = Flask(__name__)
    CORS(app)
    lock = threading.Lock()          # serialize turns (single browser)
    state = {"sent_user_turns": []}  # user turns already sent to the live convo

    def plan_send(messages):
        """Map stateless messages[] onto the stateful ChatGPT conversation.
        Returns (text_to_send, new_conversation)."""
        system = "\n\n".join(_msg_text(m) for m in messages if m.get("role") == "system").strip()
        user_turns = [_msg_text(m) for m in messages if m.get("role") == "user"]
        if not user_turns:
            return None, False
        sent = state["sent_user_turns"]
        is_cont = (len(user_turns) > len(sent) and sent and user_turns[:len(sent)] == sent)
        if is_cont:
            new_turns = user_turns[len(sent):]
            state["sent_user_turns"] = user_turns
            return "\n\n".join(new_turns), False
        state["sent_user_turns"] = user_turns
        last = user_turns[-1]
        return (f"{system}\n\n{last}" if system else last), True

    def chunk(cid, created, model, delta, finish=None):
        return "data: " + json.dumps({
            "id": cid, "object": "chat.completion.chunk", "created": created,
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }) + "\n\n"

    @app.get("/health")
    def health():
        return jsonify({
            "status": "running" if session.ready else "initializing",
            "ready": session.ready, "error": session.error,
        }), (200 if session.ready else 503)

    @app.get("/v1/models")
    def models():
        if not session.ready:
            return jsonify({"error": {"message": "session initializing"}}), 503
        data = session.list_models()
        if isinstance(data, dict) and data.get("error"):
            return jsonify({"error": {"message": data["error"]}}), 502
        out = [{
            "id": m["slug"], "object": "model", "created": 0, "owned_by": "openai",
            "_title": m.get("title"), "_max_tokens": m.get("max_tokens"),
        } for m in (data or {}).get("models", []) if m.get("slug")]
        return jsonify({"object": "list", "data": out})

    @app.post("/v1/chat/completions")
    def chat_completions():
        if not session.ready:
            return jsonify({"error": {"message": "session initializing", "type": "unavailable"}}), 503
        body = request.get_json(silent=True) or {}
        messages = body.get("messages") or []
        stream = bool(body.get("stream"))
        req_model = _norm_model(body.get("model"))

        text, new_conv = plan_send(messages)
        if not text:
            return jsonify({"error": {"message": "no user message provided"}}), 400

        roles = [m.get("role") for m in messages]
        sys_len = sum(len(_msg_text(m)) for m in messages if m.get("role") == "system")
        log.info("chat: msgs=%d roles=%s system_chars=%d tools=%d new_conv=%s -> forwarding %d chars",
                 len(messages), roles, sys_len, len(body.get("tools") or []), new_conv, len(text))
        if config.DEBUG_DUMP:
            try:
                import pathlib
                pathlib.Path("/tmp/chatgpt_proxy_last_request.json").write_text(json.dumps(
                    {"incoming_request": body, "forwarded_to_chatgpt": text,
                     "new_conversation": new_conv}, indent=2)[:400000])
            except Exception:
                pass

        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        created = int(time.time())
        resp_model = body.get("model") or "chatgpt"

        lock.acquire()
        try:
            out_q = session.submit(text, req_model, new_conv)
        except Exception as e:
            lock.release()
            return jsonify({"error": {"message": str(e)}}), 500

        if stream:
            def gen():
                try:
                    yield chunk(cid, created, resp_model, {"role": "assistant"})
                    while True:
                        ev = out_q.get()
                        if ev is None:
                            break
                        kind, val = ev
                        if kind == "content":
                            yield chunk(cid, created, resp_model, {"content": val})
                        elif kind == "reasoning":
                            yield chunk(cid, created, resp_model, {"reasoning_content": val})
                        elif kind == "error":
                            yield chunk(cid, created, resp_model,
                                        {"content": f"\n[proxy error: {val}]"}, finish="stop")
                            break
                        elif kind == "done":
                            yield chunk(cid, created, resp_model, {}, finish=val or "stop")
                            break
                    yield "data: [DONE]\n\n"
                finally:
                    lock.release()
            return Response(gen(), mimetype="text/event-stream",
                            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

        try:
            content, reasoning, finish, err = "", "", "stop", None
            while True:
                ev = out_q.get()
                if ev is None:
                    break
                kind, val = ev
                if kind == "content":
                    content += val
                elif kind == "reasoning":
                    reasoning += val
                elif kind == "done":
                    finish = val or "stop"
                elif kind == "error":
                    err = val
            if err and not content:
                return jsonify({"error": {"message": err, "type": "upstream_error"}}), 502
            msg = {"role": "assistant", "content": content}
            if reasoning:
                msg["reasoning_content"] = reasoning
            return jsonify({
                "id": cid, "object": "chat.completion", "created": created,
                "model": session.model_slug_last or resp_model,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })
        finally:
            lock.release()

    return app
