"""OpenAI Chat-Completions-shaped HTTP surface, for both providers that speak
it: chatgpt's own (stateful, tool-emulating) completions, and databricks' Azure
GPT-4.1 channel (near pass-through). Each gets its own registration function
below since the backing logic genuinely differs -- only the wire shape
(`wire.openai`) is shared.
"""

import json
import logging
import threading
import time

from flask import Response, jsonify, request

from ..application.chat import ConversationPlanner, normalize_effort
from ..domain.conversation import ChatTurn
from ..infra.logging import dump_exchange
from ..providers.chatgpt import config as chatgpt_config
from ..providers.databricks import config as databricks_config
from ..providers.databricks import llmproxy
from ..strategies import tool_calling
from ..wire import openai as wire_openai
from .health import release_lock_when_done, requires_ready

log = logging.getLogger(__name__)


def _model_slug(session):
    """The real ChatGPT model slug the last completed turn actually used
    (from the accumulator's parser), when known."""
    parser = getattr(session.last_acc, "parser", None)
    return getattr(parser, "model_slug", None) if parser else None


def _drain_full(out_q):
    """Synchronously drain a job's full event queue into (content, reasoning,
    finish, error, native_tool_calls) -- for the two non-live-streaming shapes:
    an already-decided completion, and the tool-calling path (which must see
    the whole answer before it can look for a `<tool>` block or a native
    tool-call capture)."""
    content, reasoning, finish, err, native = "", "", "stop", None, []
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
        elif kind == "tool_call":
            native.append(val)
    return content, reasoning, finish, err, native


def register_chatgpt(app, session, provider) -> None:
    """GET /v1/models, POST /v1/chat/completions (stream/non-stream + emulated
    tools). Stateful: maps the client's stateless `messages[]` onto ONE ongoing
    ChatGPT conversation via `ConversationPlanner` -- see
    `application/chat.py` and docs/discovery/."""
    lock = threading.Lock()  # serialize turns (single browser)
    planner = ConversationPlanner()

    @app.get("/v1/models")
    @requires_ready(session, wire_openai.unavailable_error)
    def models():
        data = provider.list_models(session)
        if isinstance(data, dict) and data.get("error"):
            return jsonify({"error": {"message": data["error"]}}), 502
        out = [
            {
                "id": m["slug"],
                "object": "model",
                "created": 0,
                "owned_by": "openai",
                "_title": m.get("title"),
                "_max_tokens": m.get("max_tokens"),
            }
            for m in (data or {}).get("models", [])
            if m.get("slug")
        ]
        return jsonify({"object": "list", "data": out})

    @app.post("/v1/chat/completions")
    @requires_ready(session, wire_openai.unavailable_error)
    def chat_completions():
        body = request.get_json(silent=True) or {}
        messages = body.get("messages") or []
        stream = bool(body.get("stream"))
        req_model = wire_openai.normalize_model(body.get("model"))
        effort = normalize_effort(body)

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

        text, new_conv = planner.plan_turn(
            messages, tools if tools_active else None, choice, forced_name
        )
        if not text:
            return jsonify({"error": {"message": "no user message provided"}}), 400

        roles = [m.get("role") for m in messages]
        sys_len = sum(
            len(wire_openai.message_text(m)) for m in messages if m.get("role") == "system"
        )
        log.info(
            "chat: msgs=%d roles=%s system_chars=%d tools=%d choice=%s effort=%s "
            "new_conv=%s -> forwarding %d chars",
            len(messages),
            roles,
            sys_len,
            len(tools),
            choice,
            effort,
            new_conv,
            len(text),
        )
        dump_exchange(
            "chatgpt_proxy",
            {"incoming_request": body, "forwarded_to_chatgpt": text, "new_conversation": new_conv},
            enabled=chatgpt_config.DEBUG_DUMP,
        )

        cid = wire_openai.new_id()
        created = int(time.time())
        resp_model = body.get("model") or "chatgpt"

        lock.acquire()
        try:
            out_q = session.submit(ChatTurn(text, req_model, new_conv, effort))
        except Exception as e:
            lock.release()
            return jsonify({"error": {"message": str(e)}}), 500

        if tools_active:
            try:
                content, reasoning, finish, err, native = _drain_full(out_q)
            finally:
                lock.release()
            if err and not content and not native:
                return jsonify({"error": {"message": err, "type": "upstream_error"}}), 502
            calls, leftover = tool_calling.resolve_tool_calls(
                content, native, tool_calling.tool_names(tools)
            )
            resp_model_out = _model_slug(session) or resp_model
            if calls:
                if stream:

                    def gen_tc():
                        yield wire_openai.chunk(cid, created, resp_model, {"role": "assistant"})
                        if reasoning:
                            yield wire_openai.chunk(
                                cid, created, resp_model, {"reasoning_content": reasoning}
                            )
                        d = {
                            "tool_calls": [
                                {
                                    "index": i,
                                    "id": c["id"],
                                    "type": "function",
                                    "function": c["function"],
                                }
                                for i, c in enumerate(calls)
                            ]
                        }
                        if leftover:
                            d["content"] = leftover
                        yield wire_openai.chunk(cid, created, resp_model, d)
                        yield wire_openai.chunk(cid, created, resp_model, {}, finish="tool_calls")
                        yield "data: [DONE]\n\n"

                    return Response(
                        gen_tc(),
                        mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                    )
                tc_msg = {"role": "assistant", "content": leftover or None, "tool_calls": calls}
                if reasoning:
                    tc_msg["reasoning_content"] = reasoning
                return jsonify(
                    {
                        "id": cid,
                        "object": "chat.completion",
                        "created": created,
                        "model": resp_model_out,
                        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": tc_msg}],
                        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                    }
                )
            if stream:

                def gen_txt():
                    yield wire_openai.chunk(cid, created, resp_model, {"role": "assistant"})
                    if reasoning:
                        yield wire_openai.chunk(
                            cid, created, resp_model, {"reasoning_content": reasoning}
                        )
                    if content:
                        yield wire_openai.chunk(cid, created, resp_model, {"content": content})
                    yield wire_openai.chunk(cid, created, resp_model, {}, finish=finish)
                    yield "data: [DONE]\n\n"

                return Response(
                    gen_txt(),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            msg = {"role": "assistant", "content": content}
            if reasoning:
                msg["reasoning_content"] = reasoning
            return jsonify(
                {
                    "id": cid,
                    "object": "chat.completion",
                    "created": created,
                    "model": resp_model_out,
                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            )

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
                    elif kind == "error":
                        yield wire_openai.chunk(
                            cid,
                            created,
                            resp_model,
                            {"content": f"\n[proxy error: {val}]"},
                            finish="stop",
                        )
                        break
                    elif kind == "done":
                        yield wire_openai.chunk(cid, created, resp_model, {}, finish=val or "stop")
                        break
                yield "data: [DONE]\n\n"

            return Response(
                release_lock_when_done(lock, gen()),
                mimetype="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        try:
            content, reasoning, finish, err, _native = _drain_full(out_q)
            if err and not content:
                return jsonify({"error": {"message": err, "type": "upstream_error"}}), 502
            msg = {"role": "assistant", "content": content}
            if reasoning:
                msg["reasoning_content"] = reasoning
            return jsonify(
                {
                    "id": cid,
                    "object": "chat.completion",
                    "created": created,
                    "model": _model_slug(session) or resp_model,
                    "choices": [{"index": 0, "message": msg, "finish_reason": finish}],
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                }
            )
        finally:
            lock.release()


def _collect(first, out_q) -> str:
    """Drain the remaining `("data", ...)` events (starting from `first`) off a
    job queue into one string, stopping at the first error/done/sentinel."""
    chunks = []
    ev = first
    while ev is not None:
        if ev[0] == "data":
            chunks.append(ev[1])
        elif ev[0] in ("error", "done"):
            break
        ev = out_q.get()
    return "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)


def register_databricks_openai(app, session, provider) -> None:
    """POST /v1/chat/completions -> the Azure GPT-4.1 deployments via the
    llmproxy `proxy/chat/completions` channel (near pass-through)."""

    @app.post("/v1/chat/completions")
    @requires_ready(session, wire_openai.unavailable_error)
    def chat_completions():
        """We always stream upstream (the CDP capture only reliably gets SSE,
        not a single JSON body); a client that asked for `stream:false` gets
        the chunks folded back into one completion (`wire.openai.assemble_completion`)."""
        req = request.get_json(silent=True) or {}
        want_stream = bool(req.get("stream", True))
        body, model = llmproxy.build_azure_body(req)
        log.info(
            "chat_completions: model=%s tools=%d client_stream=%s -> azure",
            model,
            len(req.get("tools") or []),
            want_stream,
        )

        out_q = session.submit({"path": databricks_config.CHAT_COMPLETIONS_PATH, "body": body})
        status, first = 200, None
        while True:
            ev = out_q.get(timeout=180)
            if ev is None:
                break
            if ev[0] == "meta":
                status = ev[1].get("status", status)
                continue
            first = ev
            break
        if first is not None and first[0] == "error":
            return jsonify({"error": {"message": first[1], "type": "upstream_error"}}), 502

        if status >= 400:
            body_txt = _collect(first, out_q)
            return Response(
                body_txt or json.dumps({"error": {"message": "upstream error", "code": status}}),
                status=status,
                content_type="application/json",
            )

        if want_stream:

            def gen():
                ev, seen_done = first, False
                while ev is not None:
                    if ev[0] == "data":
                        if "[DONE]" in ev[1]:
                            seen_done = True
                        yield ev[1]
                    elif ev[0] in ("error", "done"):
                        break
                    ev = out_q.get()
                if not seen_done:
                    yield "data: [DONE]\n\n"

            return Response(
                gen(),
                status=200,
                content_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        completion = wire_openai.assemble_completion(_collect(first, out_q), model)
        return jsonify(completion)
