"""Databricks Genie / llmproxy provider.

Two channels behind one OpenAI surface:
  - Claude ids  -> convert the OpenAI request to Anthropic Messages, POST the
    llmproxy `llmproxy/` endpoint in-page, convert the Anthropic SSE back to
    OpenAI (native tool_use -> tool_calls, extended thinking -> reasoning_content).
  - GPT ids     -> Azure `proxy/chat/completions` passthrough (already OpenAI).

No anti-bot: the fetch runs in-page (httpOnly cookie auto-attaches; CSRF read
from `/auth/session/info`). Exactly two public methods: `models()`/`completions()`.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ...gateways.cloakbrowser import BrowserSession
from ...utils import convert
from ...utils import openai as wire
from ..base import BrowserBackedProvider
from . import llmproxy, models

log = logging.getLogger(__name__)

NAME = "databricks"

_SESSION_JS = (
    "async () => { try { const r = await fetch('/auth/session/info',"
    "{credentials:'include'}); if(!r.ok) return null; "
    "return (await r.json()).userId || null; } catch(e){ return null; } }"
)

# In-page POST: read a fresh CSRF token, POST to arg.path, drain the body so the
# response completes (loadingFinished fires) instead of lingering open.
_START_JS = r"""
async (arg) => {
  const body = arg.body;
  const org = arg.org || (new URLSearchParams(location.search).get('o') || '');
  const s = await (await fetch('/auth/session/info', {credentials:'include'})).json();
  window.__dbx = (async () => {
    const res = await fetch(arg.path, {method:'POST', credentials:'include',
      headers:{'content-type':'application/json', 'accept':'text/event-stream',
               'x-csrf-token': s.csrfToken, 'x-databricks-org-id': String(org)},
      body: JSON.stringify(body)});
    const reader = res.body.getReader();
    while (true) { const {done} = await reader.read(); if (done) break; }
  })();
  return true;
}
"""

# Live model discovery. Replays the pinned, server-safelisted
# `ConversationModelStatuses` operation verbatim (query / clientIds / operationId
# from models.discovery_request(); the op-id signs the whole operation+variables,
# so it can't be trimmed or reformatted -- a 400 "operation not authentic" means
# it drifted and must be re-captured). The response covers every clientId; we
# filter in-page to the one we drive requests as, so only a small slice crosses
# the CDP boundary. models.parse_model_statuses then keeps the AVAILABLE names.
MODELS_JS = r"""
async (arg) => {
  try {
    const s = await (await fetch('/auth/session/info', {credentials:'include'})).json();
    const res = await fetch('/graphql/ConversationModelStatuses', {method:'POST',
      credentials:'include',
      headers:{'content-type':'application/json', 'accept':'*/*',
               'x-csrf-token': s.csrfToken, 'x-databricks-org-id': String(arg.org),
               'x-databricks-self': 'true',
               'x-databricks-operation-identifier': arg.operationId},
      body: JSON.stringify({operationName: arg.operationName,
                            variables: {input: {clientIds: arg.clientIds}},
                            query: arg.query})});
    if (!res.ok) return {error: res.status};
    const j = await res.json();
    const cav = ((j || {}).data || {}).conversationListModelAvailability || {};
    const all = cav.modelAvailability || [];
    const mine = all.filter((a) => a && a.clientId === arg.clientId);
    return {data: {conversationListModelAvailability: {modelAvailability: mine}}};
  } catch (e) { return {error: String(e)}; }
}
"""


def _discovery_arg(org: str) -> dict:
    req = models.discovery_request()
    return {
        "org": org,
        "clientId": llmproxy.CLIENT_ID,
        "operationName": req["operationName"],
        "operationId": req["operationId"],
        "clientIds": req["clientIds"],
        "query": req["query"],
    }


def authed(page) -> bool:
    try:
        return bool(page.evaluate(_SESSION_JS))
    except Exception:
        return False


def build_session(headless: bool, profile_dir: Path, workspace_url: str) -> BrowserSession:
    if not workspace_url:
        raise RuntimeError("databricks workspace_url is not set (workspace URL with ?o=).")
    return BrowserSession(
        name=NAME,
        nav_url=workspace_url,
        profile_dir=profile_dir,
        headless=headless,
        authed=authed,
    )


# Only the no-model-specified degenerate case; the gateway always routes a
# concrete `databricks__<slug>` here, so this is a last-resort safety net, not a
# model list. Real ids come from live discovery (models()).
_DEFAULT_MODEL = "claude-4-5-sonnet"


def _org_id(workspace_url: str) -> str:
    q = parse_qs(urlsplit(workspace_url).query)
    return (q.get("o") or [""])[0]


def _is_gpt_model(model: str) -> bool:
    """GPT family -> Azure `proxy/chat/completions` channel; else the Claude
    (Anthropic Messages) channel."""
    return model.lower().startswith("gpt")


class DatabricksProvider(BrowserBackedProvider):
    name = NAME

    def __init__(
        self,
        session: BrowserSession,
        *,
        workspace_url: str,
        style_rules: bool = True,
    ):
        super().__init__(session)
        self._lock = threading.Lock()
        self._workspace_url = workspace_url
        self._style_rules = style_rules

    # ---- models -----------------------------------------------------------
    def models(self) -> list[dict]:
        """Every model live-discovered as AVAILABLE for the editor-assistant-agent-mode
        clientId, listed verbatim as `databricks__<name>` -- no family mapping, no
        filtering, no capability guessing. Empty on discovery failure."""
        return [
            {
                "id": wire.join_model(NAME, name),
                "object": "model",
                "created": 0,
                "owned_by": "databricks",
            }
            for name in self._discover_models()
        ]

    def _discover_models(self) -> list[str]:
        try:
            data = self.session.evaluate(MODELS_JS, _discovery_arg(_org_id(self._workspace_url)))
        except Exception:
            log.warning("databricks ConversationModelStatuses probe failed", exc_info=True)
            return []
        if not isinstance(data, dict) or data.get("error"):
            err = data.get("error") if isinstance(data, dict) else data
            log.warning("databricks model discovery error: %s", err)
            return []
        names = models.parse_model_statuses(data, llmproxy.CLIENT_ID)
        if not names:
            log.warning("databricks model discovery: no AVAILABLE models parsed")
        return names

    # ---- completions ------------------------------------------------------
    def completions(self, request: dict):
        model = (request.get("model") or "").strip() or _DEFAULT_MODEL
        if _is_gpt_model(model):
            return self._azure_completions(request, model)
        return self._claude_completions(request, model)

    # ---- Claude channel ---------------------------------------------------
    def _claude_completions(self, request: dict, model: str):
        stream = bool(request.get("stream"))
        effort = wire.normalize_effort(request)
        anthropic_body = convert.openai_to_anthropic(
            request, default_max_tokens=llmproxy.CLAUDE_MAX_TOKENS, effort=effort
        )
        body = llmproxy.build_llmproxy_envelope(
            anthropic_body, model, style_rules=self._style_rules
        )
        cid = wire.new_id()
        created = int(time.time())
        resp_model = wire.join_model(NAME, model)

        self._lock.acquire()
        try:
            out_q = self.session.run_turn(
                trigger=self._make_trigger(llmproxy.LLMPROXY_PATH, body),
                capture_url=lambda url: url.split("?", 1)[0].endswith(llmproxy.LLMPROXY_PATH),
                parse=convert.AnthropicSSE(),
            )
        except Exception as e:
            self._lock.release()
            return {"error": {"message": str(e)}}

        if stream:
            return self._stream_claude(out_q, cid, created, resp_model)
        try:
            return self._nonstream_claude(out_q, cid, created, resp_model)
        finally:
            self._lock.release()

    def _make_trigger(self, path: str, body: dict):
        org = _org_id(self._workspace_url)

        def trigger(page):
            page.evaluate(_START_JS, {"path": path, "body": body, "org": org})

        return trigger

    def _nonstream_claude(self, out_q, cid, created, model) -> dict:
        content, reasoning, finish, err = "", "", "stop", None
        tool_calls: dict = {}
        while True:
            ev = out_q.get()
            if ev is None:
                break
            kind, val = ev
            if kind == "content":
                content += val
            elif kind == "reasoning":
                reasoning += val
            elif kind == "tool_start":
                tool_calls[val["index"]] = {
                    "id": val["id"],
                    "type": "function",
                    "function": {"name": val["name"], "arguments": ""},
                }
            elif kind == "tool_args":
                if val["index"] in tool_calls:
                    tool_calls[val["index"]]["function"]["arguments"] += val["partial_json"]
            elif kind == "done":
                finish = val or "stop"
            elif kind == "error":
                err = val
        if err and not content and not tool_calls:
            return {"error": {"message": err, "type": "upstream_error"}}
        msg = {"role": "assistant", "content": content or None}
        if reasoning:
            msg["reasoning_content"] = reasoning
        if tool_calls:
            msg["tool_calls"] = [tool_calls[k] for k in sorted(tool_calls)]
            finish = "tool_calls"
        return wire.completion(cid, created, model, msg, finish)

    def _stream_claude(self, out_q, cid, created, model) -> Iterator[str]:
        def gen():
            try:
                yield wire.chunk(cid, created, model, {"role": "assistant"})
                tool_idx = {}
                while True:
                    ev = out_q.get()
                    if ev is None:
                        break
                    kind, val = ev
                    if kind == "content":
                        yield wire.chunk(cid, created, model, {"content": val})
                    elif kind == "reasoning":
                        yield wire.chunk(cid, created, model, {"reasoning_content": val})
                    elif kind == "tool_start":
                        i = len(tool_idx)
                        tool_idx[val["index"]] = i
                        yield wire.chunk(
                            cid,
                            created,
                            model,
                            {
                                "tool_calls": [
                                    {
                                        "index": i,
                                        "id": val["id"],
                                        "type": "function",
                                        "function": {"name": val["name"], "arguments": ""},
                                    }
                                ]
                            },
                        )
                    elif kind == "tool_args":
                        i = tool_idx.get(val["index"], 0)
                        yield wire.chunk(
                            cid,
                            created,
                            model,
                            {
                                "tool_calls": [
                                    {"index": i, "function": {"arguments": val["partial_json"]}}
                                ]
                            },
                        )
                    elif kind == "error":
                        yield wire.chunk(cid, created, model, {}, finish="stop")
                        break
                    elif kind == "done":
                        yield wire.chunk(cid, created, model, {}, finish=val or "stop")
                        break
                yield "data: [DONE]\n\n"
            finally:
                self._lock.release()

        return gen()

    # ---- Azure GPT channel ------------------------------------------------
    def _azure_completions(self, request: dict, model: str):
        stream = bool(request.get("stream", True))
        body = llmproxy.build_azure_body(request, model)
        resp_model = wire.join_model(NAME, model)

        self._lock.acquire()
        try:
            out_q = self.session.run_turn(
                trigger=self._make_trigger(llmproxy.CHAT_COMPLETIONS_PATH, body),
                capture_url=lambda url: url.split("?", 1)[0].endswith(
                    llmproxy.CHAT_COMPLETIONS_PATH
                ),
                parse=_PassthroughParse(),
            )
        except Exception as e:
            self._lock.release()
            return {"error": {"message": str(e)}}

        if stream:
            return self._stream_azure(out_q)
        try:
            sse = _collect_data(out_q)
            return wire.assemble_completion(sse, resp_model)
        finally:
            self._lock.release()

    def _stream_azure(self, out_q) -> Iterator[str]:
        def gen():
            try:
                seen_done = False
                while True:
                    ev = out_q.get()
                    if ev is None:
                        break
                    if ev[0] == "data":
                        if "[DONE]" in ev[1]:
                            seen_done = True
                        yield ev[1]
                    elif ev[0] in ("error", "done"):
                        break
                if not seen_done:
                    yield "data: [DONE]\n\n"
            finally:
                self._lock.release()

        return gen()


class _PassthroughParse:
    """Forward raw captured SSE bytes unchanged as ("data", text)."""

    finish_reason = "stop"

    def feed(self, chunk: str):
        return [("data", chunk)] if chunk else []

    def flush(self):
        return []


def _collect_data(out_q) -> str:
    chunks = []
    while True:
        ev = out_q.get()
        if ev is None:
            break
        if ev[0] == "data":
            chunks.append(ev[1])
        elif ev[0] in ("error", "done"):
            break
    return "".join(c.decode() if isinstance(c, bytes) else c for c in chunks)
