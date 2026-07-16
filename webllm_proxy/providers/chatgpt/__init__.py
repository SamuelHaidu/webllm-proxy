"""ChatGPT web provider: OpenAI-compatible surface backed by a logged-in
chatgpt.com session. Tools are emulated via the tag contract; reasoning maps
onto ChatGPT web's `thinking_effort`; the model is forced by rewriting the
`f/conversation` request body via CDP Fetch. Exactly two public methods:
`models()` and `completions()`.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from ...gateways.cloakbrowser import BrowserSession
from ...utils import openai as wire
from ...utils import tags
from ...utils.prompts import default_store
from ..base import BrowserBackedProvider
from .research import run_research
from .sse import StreamAccumulator

log = logging.getLogger(__name__)

CHATGPT_URL = "https://chatgpt.com"
NAME = "chatgpt"
RESEARCH_MODEL = "research"
AUTO_MODEL = "auto"

_MODELS_JS = """async () => {
  const s = await (await fetch('/api/auth/session')).json();
  const t = s.accessToken;
  const r = await fetch('/backend-api/models', {headers: {'Authorization': 'Bearer ' + t}});
  if (!r.ok) return {error: 'models ' + r.status};
  return await r.json();
}"""

_AUTH_JS = """async () => {
  try { const s = await (await fetch('/api/auth/session')).json();
        return !!s.accessToken; } catch (e) { return false; }
}"""


def authed(page) -> bool:
    try:
        return bool(page.evaluate(_AUTH_JS))
    except Exception:
        return False


def build_session(
    headless: bool, profile_dir: Path, extension_paths: list[str] | None = None
) -> BrowserSession:
    return BrowserSession(
        name=NAME,
        nav_url=CHATGPT_URL + "/",
        profile_dir=profile_dir,
        headless=headless,
        authed=authed,
        fetch_patterns=[{"urlPattern": "*/backend-api/f/conversation*", "requestStage": "Request"}],
        extension_paths=extension_paths,
    )


# ---- effort support -------------------------------------------------------
def _effort_values(entries) -> set[str]:
    vals: set[str] = set()
    for e in entries:
        if isinstance(e, str):
            vals.add(e)
        elif isinstance(e, dict):
            v = e.get("thinking_effort") or e.get("effort") or e.get("id") or e.get("value")
            if isinstance(v, str):
                vals.add(v)
    return vals


def _model_effort_entry(m):
    if not (isinstance(m, dict) and m.get("configurable_thinking_effort") and m.get("slug")):
        return None
    vals = _effort_values(m.get("thinking_efforts") or [])
    return (m["slug"], vals) if vals else None


# ---- conversation continuity ----------------------------------------------
def _message_signature(m: dict):
    role = m.get("role")
    if role == "assistant" and m.get("tool_calls"):
        return ("a_tc", json.dumps(m.get("tool_calls"), sort_keys=True, default=str))
    if role == "tool":
        return ("tool", m.get("tool_call_id"), wire.message_text(m))
    return (role, wire.message_text(m))


def _format_turns(msgs, name_map) -> str:
    out = []
    for m in msgs:
        role = m.get("role")
        if role == "user":
            out.append(wire.message_text(m))
        elif role == "tool":
            out.append(tags.format_tool_result(m, name_map))
    return "\n\n".join(t for t in out if t).strip()


class _Planner:
    def __init__(self):
        self._sigs: list = []

    def plan_turn(self, messages, tools, tool_choice, forced_name, system_text=None):
        sigs = [_message_signature(m) for m in messages]
        prev = self._sigs
        continuing = bool(prev) and len(sigs) > len(prev) and sigs[: len(prev)] == prev
        self._sigs = sigs
        name_map = tags.tool_name_map(messages)
        if continuing:
            return _format_turns(messages[len(prev) :], name_map) or None, False
        # The client's own `role:"system"` messages are always ignored -- only
        # a configured `system_text` (resolved from `utils.config` by the
        # provider) is ever sent, see docs/discovery/2026-07-13-system-prompt-architecture.md.
        # If no system prompt is configured for this model, send NOTHING at
        # all -- not even the tool contract -- so tool-calling emulation for
        # chatgpt only activates once the operator explicitly sets a
        # `system_prompt` for this provider/model.
        preamble = (
            tags.build_preamble(system_text, tools, tool_choice, forced_name) if system_text else ""
        )
        last_user = max(
            (i for i, m in enumerate(messages) if m.get("role") == "user"), default=None
        )
        tail = messages[last_user:] if last_user is not None else messages
        body = _format_turns(tail, name_map)
        if not preamble:
            return (body or None), True
        framing = default_store.get("user_request_framing")
        return ((preamble + "\n\n" + framing + "\n\n" + body).strip() or None), True


def _find_composer(page):
    for sel in ("#prompt-textarea", 'div[contenteditable="true"]', "textarea"):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            pass
    return None


def _trigger(page, *, new_conversation: bool, message: str):
    if new_conversation:
        page.goto(CHATGPT_URL + "/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1200)
    comp = _find_composer(page)
    if not comp:
        raise RuntimeError("composer not found")
    comp.click()
    page.wait_for_timeout(150)
    page.keyboard.insert_text(message)
    page.wait_for_timeout(150)
    page.keyboard.press("Enter")


class ChatgptProvider(BrowserBackedProvider):
    name = NAME

    def __init__(self, session: BrowserSession, *, system_prompt=None, user_suffix=None):
        super().__init__(session)
        self._lock = threading.Lock()
        self._planner = _Planner()
        self._effort_support: dict[str, set[str]] | None = None
        # `(slug | None) -> prompt-name | None`, e.g.
        # `ProviderConfigBase.system_prompt_for` -- resolves which named
        # `prompts/system_prompts/<name>.md` (if any) to send for a given
        # model slug. Defaults to "never send one" if not wired in.
        self._system_prompt = system_prompt or (lambda _slug: None)
        # Same shape, `ProviderConfigBase.user_suffix_for` -- text appended to
        # the end of THIS turn's message before it's sent (a per-turn "stay in
        # role" nudge). Defaults to "never append one" if not wired in.
        self._user_suffix = user_suffix or (lambda _slug: None)

    # ---- effort support (lazy) -------------------------------------------
    def _load_effort_support(self) -> dict[str, set[str]]:
        if self._effort_support is not None:
            return self._effort_support
        data = self.session.evaluate(_MODELS_JS)
        support: dict[str, set[str]] = {}
        if isinstance(data, dict):
            for m in data.get("models") or []:
                entry = _model_effort_entry(m)
                if entry:
                    support[entry[0]] = entry[1]
        self._effort_support = support
        return support

    def _fetch_rewrite(self, forced_model, forced_effort):
        support = self._load_effort_support()

        def rewrite(post: str):
            try:
                b = json.loads(post)
            except json.JSONDecodeError:
                return None
            changed = False
            if forced_model:
                b["model"] = forced_model
                changed = True
            if forced_effort:
                allowed = support.get(b.get("model"))
                if allowed and forced_effort in allowed:
                    b["thinking_effort"] = forced_effort
                    changed = True
            return json.dumps(b) if changed else None

        return rewrite

    # ---- models -----------------------------------------------------------
    def models(self) -> list[dict]:
        data = self.session.evaluate(_MODELS_JS)
        out = [
            {
                "id": wire.join_model(NAME, AUTO_MODEL),
                "object": "model",
                "created": 0,
                "owned_by": "openai",
                "_title": "Auto (ChatGPT picks the model, like the web UI's own Auto option)",
            }
        ]
        if isinstance(data, dict) and not data.get("error"):
            for m in data.get("models") or []:
                slug = m.get("slug")
                if not slug:
                    continue
                out.append(
                    {
                        "id": wire.join_model(NAME, slug),
                        "object": "model",
                        "created": 0,
                        "owned_by": "openai",
                        "_title": m.get("title"),
                        "_max_tokens": m.get("max_tokens"),
                    }
                )
        out.append(
            {
                "id": wire.join_model(NAME, RESEARCH_MODEL),
                "object": "model",
                "created": 0,
                "owned_by": "openai",
                "_title": "Emulated web research",
            }
        )
        return out

    # ---- completions ------------------------------------------------------
    def completions(self, request: dict):
        model = (request.get("model") or "").strip()
        if model == RESEARCH_MODEL:
            return run_research(self.session, self._lock, request)

        messages = request.get("messages") or []
        stream = bool(request.get("stream"))
        req_model = _normalize_model(model)
        effort = wire.normalize_effort(request)

        tools = request.get("tools") or []
        raw_choice = request.get("tool_choice")
        forced_name = None
        if isinstance(raw_choice, dict):
            forced_name = (raw_choice.get("function") or {}).get("name")
            choice = "required"
        elif isinstance(raw_choice, str):
            choice = raw_choice
        else:
            choice = "auto" if tools else "none"
        tools_active = bool(tools) and choice != "none"

        prompt_name = self._system_prompt(model or None)
        sys_text = default_store.get(prompt_name) if prompt_name else None

        text, new_conv = self._planner.plan_turn(
            messages, tools if tools_active else None, choice, forced_name, sys_text
        )
        if not text:
            return {"error": {"message": "no user message provided"}}
        suffix_text = self._user_suffix(model or None)
        if suffix_text:
            text = f"{text}\n\n{suffix_text}".strip()

        cid = wire.new_id()
        created = int(time.time())
        resp_model = wire.join_model(NAME, model or "chatgpt")

        self._lock.acquire()
        try:
            out_q = self.session.run_turn(
                trigger=lambda page: _trigger(page, new_conversation=new_conv, message=text),
                capture_url=lambda url: url.split("?", 1)[0].endswith("/f/conversation"),
                parse=StreamAccumulator(),
                fetch_rewrite=self._fetch_rewrite(req_model, effort),
            )
        except Exception as e:
            self._lock.release()
            return {"error": {"message": str(e)}}

        used_tools = tools if tools_active else None
        if tools_active:
            try:
                result = self._tool_response(
                    out_q, cid, created, resp_model, stream, tags.tool_names(tools)
                )
                return wire.attach_usage(result, messages, used_tools, resp_model)
            finally:
                if not stream:
                    self._lock.release()

        if stream:
            return self._stream_text(out_q, cid, created, resp_model)
        try:
            result = self._nonstream_text(out_q, cid, created, resp_model)
            return wire.attach_usage(result, messages, used_tools, resp_model)
        finally:
            self._lock.release()

    # ---- response shaping -------------------------------------------------
    def _stream_text(self, out_q, cid, created, model) -> Iterator[str]:
        def gen():
            try:
                yield wire.chunk(cid, created, model, {"role": "assistant"})
                while True:
                    ev = out_q.get()
                    if ev is None:
                        break
                    kind, val = ev
                    if kind == "content":
                        yield wire.chunk(cid, created, model, {"content": val})
                    elif kind == "reasoning":
                        yield wire.chunk(cid, created, model, {"reasoning_content": val})
                    elif kind == "error":
                        yield wire.chunk(
                            cid,
                            created,
                            model,
                            {"content": f"\n[proxy error: {val}]"},
                            finish="stop",
                        )
                        break
                    elif kind == "done":
                        yield wire.chunk(cid, created, model, {}, finish=val or "stop")
                        break
                yield "data: [DONE]\n\n"
            finally:
                self._lock.release()

        return gen()

    def _nonstream_text(self, out_q, cid, created, model) -> dict:
        content, reasoning, finish, err, _ = _drain_full(out_q)
        if err and not content:
            return {"error": {"message": err, "type": "upstream_error"}}
        msg = {"role": "assistant", "content": content}
        if reasoning:
            msg["reasoning_content"] = reasoning
        return wire.completion(cid, created, model, msg, finish)

    def _tool_response(self, out_q, cid, created, model, stream, allowed_names):
        content, reasoning, finish, err, _ = _drain_full(out_q)
        if err and not content:
            return {"error": {"message": err, "type": "upstream_error"}}
        calls, leftover = tags.parse_tool_calls(content)
        if stream:
            return self._stream_tools(cid, created, model, calls, leftover, reasoning, finish)
        if calls:
            msg = {"role": "assistant", "content": leftover or None, "tool_calls": calls}
            if reasoning:
                msg["reasoning_content"] = reasoning
            return wire.completion(cid, created, model, msg, "tool_calls")
        msg = {"role": "assistant", "content": content}
        if reasoning:
            msg["reasoning_content"] = reasoning
        return wire.completion(cid, created, model, msg, finish)

    def _stream_tools(
        self, cid, created, model, calls, leftover, reasoning, finish
    ) -> Iterator[str]:
        def gen():
            try:
                yield wire.chunk(cid, created, model, {"role": "assistant"})
                if reasoning:
                    yield wire.chunk(cid, created, model, {"reasoning_content": reasoning})
                if calls:
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
                    yield wire.chunk(cid, created, model, d)
                    yield wire.chunk(cid, created, model, {}, finish="tool_calls")
                else:
                    if leftover:
                        yield wire.chunk(cid, created, model, {"content": leftover})
                    yield wire.chunk(cid, created, model, {}, finish=finish)
                yield "data: [DONE]\n\n"
            finally:
                self._lock.release()

        return gen()


def _normalize_model(model: str | None) -> str | None:
    if not model:
        return None
    if model.strip().lower() in (AUTO_MODEL, "default", "chatgpt", "gpt", ""):
        return None
    return model.strip()


def _drain_full(out_q):
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
    return content, reasoning, finish, err, []
