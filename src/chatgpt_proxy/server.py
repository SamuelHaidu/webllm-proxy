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
from . import tools as tools_mod
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
    lock = threading.Lock()   # serialize turns (single browser)
    state = {"sigs": []}      # signature of the messages[] already accounted for

    def _sig(m):
        role = m.get("role")
        if role == "assistant" and m.get("tool_calls"):
            return ("a_tc", json.dumps(m.get("tool_calls"), sort_keys=True, default=str))
        if role == "tool":
            return ("tool", m.get("tool_call_id"), _msg_text(m))
        return (role, _msg_text(m))

    def _format_turns(msgs, name_map):
        """Render the user/tool messages of a slice as text for ChatGPT
        (assistant/system messages are ChatGPT's own output or go in the
        preamble, so they are not echoed back)."""
        out = []
        for m in msgs:
            role = m.get("role")
            if role == "user":
                out.append(_msg_text(m))
            elif role == "tool":
                out.append(tools_mod.format_tool_result(m, name_map))
        return "\n\n".join(t for t in out if t).strip()

    def plan_turn(messages, tools, tool_choice, forced_name):
        """Map stateless messages[] onto the stateful ChatGPT conversation.
        Continuation = the prior signature is a prefix of this one (send only
        what's new); otherwise start a fresh ChatGPT conversation.
        Returns (text_to_send, new_conversation)."""
        sigs = [_sig(m) for m in messages]
        prev = state["sigs"]
        cont = bool(prev) and len(sigs) > len(prev) and sigs[:len(prev)] == prev
        state["sigs"] = sigs
        name_map = tools_mod.tool_name_map(messages)
        if cont:
            return _format_turns(messages[len(prev):], name_map) or None, False
        # new conversation: preamble (system + tool contract) + the newest turn(s)
        system_text = "\n\n".join(_msg_text(m) for m in messages if m.get("role") == "system")
        preamble = tools_mod.build_preamble(system_text, tools, tool_choice, forced_name)
        last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=None)
        tail = messages[last_user:] if last_user is not None else messages
        body = _format_turns(tail, name_map)
        text = (preamble + "\n\n" + body).strip() if preamble else body
        return (text or None), True

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

        tools = body.get("tools") or []
        raw_choice = body.get("tool_choice")
        forced_name = None
        if isinstance(raw_choice, dict):
            forced_name = (raw_choice.get("function") or {}).get("name")
            choice = "required"
        elif isinstance(raw_choice, str):
            choice = raw_choice
        else:
            choice = "auto" if tools else "none"
        tools_active = bool(tools) and choice != "none"

        text, new_conv = plan_turn(messages, tools if tools_active else None, choice, forced_name)
        if not text:
            return jsonify({"error": {"message": "no user message provided"}}), 400

        roles = [m.get("role") for m in messages]
        sys_len = sum(len(_msg_text(m)) for m in messages if m.get("role") == "system")
        log.info("chat: msgs=%d roles=%s system_chars=%d tools=%d choice=%s new_conv=%s -> forwarding %d chars",
                 len(messages), roles, sys_len, len(tools), choice, new_conv, len(text))
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

        if tools_active:
            # Tool calls can't be recognized until the reply is complete, so
            # buffer the whole turn, then emit either tool_calls or content.
            content, reasoning, finish, err = "", "", "stop", None
            try:
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
            finally:
                lock.release()
            if err and not content:
                return jsonify({"error": {"message": err, "type": "upstream_error"}}), 502
            calls, leftover = tools_mod.parse_tool_calls(content)
            resp_model_out = session.model_slug_last or resp_model
            if calls:
                if stream:
                    def gen_tc():
                        yield chunk(cid, created, resp_model, {"role": "assistant"})
                        d = {"tool_calls": [{
                            "index": i, "id": c["id"], "type": "function",
                            "function": c["function"],
                        } for i, c in enumerate(calls)]}
                        if leftover:
                            d["content"] = leftover
                        yield chunk(cid, created, resp_model, d)
                        yield chunk(cid, created, resp_model, {}, finish="tool_calls")
                        yield "data: [DONE]\n\n"
                    return Response(gen_tc(), mimetype="text/event-stream",
                                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
                return jsonify({
                    "id": cid, "object": "chat.completion", "created": created,
                    "model": resp_model_out,
                    "choices": [{"index": 0, "finish_reason": "tool_calls",
                                 "message": {"role": "assistant",
                                             "content": leftover or None, "tool_calls": calls}}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                })
            # no tool call -> ordinary answer (delivered buffered)
            if stream:
                def gen_txt():
                    yield chunk(cid, created, resp_model, {"role": "assistant"})
                    if reasoning:
                        yield chunk(cid, created, resp_model, {"reasoning_content": reasoning})
                    if content:
                        yield chunk(cid, created, resp_model, {"content": content})
                    yield chunk(cid, created, resp_model, {}, finish=finish)
                    yield "data: [DONE]\n\n"
                return Response(gen_txt(), mimetype="text/event-stream",
                                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
            msg = {"role": "assistant", "content": content}
            if reasoning:
                msg["reasoning_content"] = reasoning
            return jsonify({
                "id": cid, "object": "chat.completion", "created": created,
                "model": resp_model_out,
                "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            })

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
