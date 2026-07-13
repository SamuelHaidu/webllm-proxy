"""`chatgpt__research` as a model: one long ChatGPT turn asking for web-search-
backed, structured-markdown research (no Deep Research trigger; works on any
account). Replaces the old async research job API/CLI -- it's just a completion
with much longer timeout caps and a research prompt.
"""

from __future__ import annotations

import time

from ...utils import openai as wire
from ...utils.prompts import default_store

RESEARCH_SLUG = "gpt-5-mini"
IDLE_CAP_S = 120.0
HARD_CAP_S = 1200.0


def _build_message(query: str) -> str:
    return (
        default_store.get("research_emulated")
        + "\n\n"
        + default_store.get("research_report", query=query)
        + "\n\n# Research request\n"
        + query
    )


def _query(messages) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return wire.message_text(m)
    return ""


def run_research(session, lock, request: dict):
    from . import _trigger
    from .sse import StreamAccumulator

    query = _query(request.get("messages") or [])
    stream = bool(request.get("stream"))
    message = _build_message(query)
    cid = wire.new_id()
    created = int(time.time())
    model = wire.join_model("chatgpt", "research")

    lock.acquire()
    try:
        out_q = session.run_turn(
            trigger=lambda page: _trigger(page, new_conversation=True, message=message),
            capture_url=lambda url: url.split("?", 1)[0].endswith("/f/conversation"),
            parse=StreamAccumulator(),
            fetch_rewrite=_force_model(RESEARCH_SLUG),
            idle_cap_s=IDLE_CAP_S,
            hard_cap_s=HARD_CAP_S,
        )
    except Exception as e:
        lock.release()
        return {"error": {"message": str(e)}}

    if stream:
        return _stream(out_q, lock, cid, created, model)
    try:
        content, err = "", None
        while True:
            ev = out_q.get()
            if ev is None:
                break
            if ev[0] == "content":
                content += ev[1]
            elif ev[0] == "error":
                err = ev[1]
        if err and not content:
            return {"error": {"message": err, "type": "upstream_error"}}
        return wire.completion(
            cid, created, model, {"role": "assistant", "content": content.strip()}, "stop"
        )
    finally:
        lock.release()


def _force_model(slug):
    import json

    def rewrite(post: str):
        try:
            b = json.loads(post)
        except json.JSONDecodeError:
            return None
        b["model"] = slug
        return json.dumps(b)

    return rewrite


def _stream(out_q, lock, cid, created, model):
    def gen():
        try:
            yield wire.chunk(cid, created, model, {"role": "assistant"})
            while True:
                ev = out_q.get()
                if ev is None:
                    break
                if ev[0] == "content":
                    yield wire.chunk(cid, created, model, {"content": ev[1]})
                elif ev[0] == "error" or ev[0] == "done":
                    yield wire.chunk(cid, created, model, {}, finish="stop")
                    break
            yield "data: [DONE]\n\n"
        finally:
            lock.release()

    return gen()
