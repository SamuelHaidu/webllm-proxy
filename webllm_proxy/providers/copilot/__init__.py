"""Microsoft Copilot (M365 BizChat) provider: OpenAI-compatible surface backed
by a logged-in m365.cloud.microsoft session. Turns run through the page (type
into the composer; the page opens its ChatHub WebSocket; we capture the frames
over CDP). Tools are emulated via the tag contract (copilot has no client-
declarable tools). Effort has no API param, so it's exposed as model variations
(`copilot__<id>`), live-discovered from the RefreshNavPane capability manifest
-- no static list. Exactly two public methods: `models()`/`completions()`.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Iterator
from pathlib import Path

from ...gateways.cloakbrowser import BrowserSession
from ...utils import openai as wire
from ...utils import tags
from ..base import BrowserBackedProvider
from . import models
from .signalr import SignalRParse

log = logging.getLogger(__name__)

NAME = "copilot"
# Drive login/boot at the chat surface, not the site root: the root
# `m365.cloud.microsoft/` serves a signed-out marketing splash from the *app*
# host (so it looks "authed" by hostname), whereas `/chat` a logged-out session
# cannot reach -- it 302s to a `login.*` host. That asymmetry is what makes
# auth detection reliable (see authed() / login_steer below).
NAV_URL = "https://m365.cloud.microsoft/chat"
_CHATHUB_MATCH = ("/m365Copilot/Chathub", "/c/api/chat")


_LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "login.microsoft.com")
_APP_HOSTS = ("m365.cloud.microsoft", "copilot.microsoft.com")

# True iff a usable Copilot message composer is actually on the page (locale- and
# edition-agnostic; the signed-out splash and office.com home have none).
_COMPOSER_JS = r"""
() => {
  const sel = '[role="textbox"], [contenteditable="true"], textarea,'
            + ' #m365-chat-editor-target-element';
  return Array.from(document.querySelectorAll(sel))
    .some(el => (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
}
"""


def authed(page) -> bool:
    """True only for a *signed-in* Copilot app page. The page title is useless
    here (the marketing homepage keeps "... - Sign in" even when logged in), so we
    key on what a logged-out session cannot produce: being on the `/chat` app path
    (it 302s to a `login.*` host when unauthenticated) or an actual composer being
    present. Reject `login.*` hosts and non-app hosts (e.g. the office.com landing
    a login sometimes drops onto)."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return False
    if any(h in url for h in _LOGIN_HOSTS):
        return False
    if not any(h in url for h in _APP_HOSTS):
        return False
    if "/chat" in url:
        return True
    try:
        return bool(page.evaluate(_COMPOSER_JS))
    except Exception:
        return False


def login_steer(page) -> None:
    """Login-poll helper: if the browser has settled off both the identity
    provider and the chat app (e.g. lands on office.com after a successful auth),
    steer it back to the chat surface so `authed()` can confirm the session. Never
    fires while the user is still on a `login.*` page, so it can't disrupt sign-in."""
    try:
        url = (page.url or "").lower()
    except Exception:
        return
    if any(h in url for h in _LOGIN_HOSTS):
        return
    if not (any(h in url for h in _APP_HOSTS) and "/chat" in url):
        with contextlib.suppress(Exception):
            page.goto(NAV_URL, wait_until="domcontentloaded", timeout=60000)


def build_session(headless: bool, profile_dir: Path, nav_url: str = NAV_URL) -> BrowserSession:
    return BrowserSession(
        name=NAME,
        nav_url=nav_url,
        profile_dir=profile_dir,
        headless=headless,
        authed=authed,
    )


def _find_composer(page):
    for sel in (
        '[role="textbox"]',
        'div[contenteditable="true"]',
        "textarea",
        "#m365-chat-editor-target-element",
    ):
        loc = page.locator(sel).first
        try:
            if loc.count() and loc.is_visible():
                return loc
        except Exception:
            pass
    return None


def _trigger(page, *, message: str, nav_url: str):
    page.goto(nav_url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(1500)
    comp = _find_composer(page)
    if comp is None:
        raise RuntimeError("copilot composer not found")
    comp.click()
    page.wait_for_timeout(150)
    page.keyboard.insert_text(message)
    page.wait_for_timeout(150)
    page.keyboard.press("Enter")


def _flatten(messages) -> tuple[str, str]:
    """Collapse the OpenAI messages into (system_text, body_text). Copilot has no
    system role or continuity API, so we send one combined turn."""
    system_text = "\n\n".join(wire.message_text(m) for m in messages if m.get("role") == "system")
    name_map = tags.tool_name_map(messages)
    body_parts = []
    for m in messages:
        role = m.get("role")
        if role == "user":
            body_parts.append(wire.message_text(m))
        elif role == "tool":
            body_parts.append(tags.format_tool_result(m, name_map))
    return system_text, "\n\n".join(p for p in body_parts if p).strip()


class CopilotProvider(BrowserBackedProvider):
    name = NAME

    def __init__(self, session: BrowserSession, *, nav_url: str = NAV_URL):
        super().__init__(session)
        self._lock = threading.Lock()
        self._nav_url = nav_url

    # ---- models -----------------------------------------------------------
    def models(self) -> list[dict]:
        """Every model live-discovered from the RefreshNavPane capability
        manifest, listed verbatim as `copilot__<id>` -- no static list, no
        family mapping/heuristics. Empty on discovery failure (logged)."""
        return [
            {
                "id": wire.join_model(NAME, item["id"]),
                "object": "model",
                "created": 0,
                "owned_by": "microsoft",
                "_title": item["title"],
            }
            for item in self._discover_models()
        ]

    def _discover_models(self) -> list[dict]:
        try:
            data = self.session.evaluate(models.MANIFEST_JS)
        except Exception:
            log.warning("copilot model discovery probe failed", exc_info=True)
            return []
        if not isinstance(data, dict) or data.get("error"):
            err = data.get("error") if isinstance(data, dict) else data
            log.warning("copilot model discovery error: %s", err)
            return []
        items = models.parse_manifest(data)
        if not items:
            log.warning("copilot model discovery: no models parsed")
        return items

    # ---- completions ------------------------------------------------------
    def completions(self, request: dict):
        messages = request.get("messages") or []
        stream = bool(request.get("stream"))
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

        system_text, body = _flatten(messages)
        if tools_active:
            # Copilot's own model is safety-tuned against believing an absolute
            # "these are your only tools, nothing else exists" claim (it knows it
            # has real server-side tools); use the milder contract wording (see
            # tags.build_preamble) instead of the chatgpt-tuned default.
            preamble = tags.build_preamble(
                system_text,
                tools,
                choice,
                forced_name,
                contract_prompt="webui_tool_contract_copilot",
                exclusive=False,
            )
            text = "\n\n".join(p for p in (preamble, body) if p).strip()
        else:
            text = "\n\n".join(p for p in (system_text, body) if p).strip()
        if not text:
            return {"error": {"message": "no user message provided"}}

        cid = wire.new_id()
        created = int(time.time())
        # Cosmetic label only (echoed in the response's "model" field): the
        # composer doesn't yet click a model selector, so whichever tone is
        # already active in the page drives the turn regardless of this string.
        resp_model = wire.join_model(NAME, request.get("model") or "default")

        self._lock.acquire()
        try:
            out_q = self.session.run_turn(
                trigger=lambda page: _trigger(page, message=text, nav_url=self._nav_url),
                capture_url=lambda url: any(s in url for s in _CHATHUB_MATCH),
                parse=SignalRParse(),
            )
        except Exception as e:
            self._lock.release()
            return {"error": {"message": str(e)}}

        if tools_active:
            try:
                return self._tool_response(out_q, cid, created, resp_model, stream)
            finally:
                if not stream:
                    self._lock.release()
        if stream:
            return self._stream_text(out_q, cid, created, resp_model)
        try:
            return self._nonstream_text(out_q, cid, created, resp_model)
        finally:
            self._lock.release()

    # ---- response shaping -------------------------------------------------
    def _drain(self, out_q):
        content, err, finish = "", None, "stop"
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

    def _nonstream_text(self, out_q, cid, created, model) -> dict:
        content, finish, err = self._drain(out_q)
        if err and not content:
            return {"error": {"message": err, "type": "upstream_error"}}
        return wire.completion(
            cid, created, model, {"role": "assistant", "content": content}, finish
        )

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

    def _tool_response(self, out_q, cid, created, model, stream):
        content, finish, err = self._drain(out_q)
        if err and not content:
            return {"error": {"message": err, "type": "upstream_error"}}
        calls, leftover = tags.parse_tool_calls(content)
        if stream:
            return self._stream_tools(cid, created, model, calls, leftover, finish)
        if calls:
            msg = {"role": "assistant", "content": leftover or None, "tool_calls": calls}
            return wire.completion(cid, created, model, msg, "tool_calls")
        return wire.completion(
            cid, created, model, {"role": "assistant", "content": content}, finish
        )

    def _stream_tools(self, cid, created, model, calls, leftover, finish) -> Iterator[str]:
        def gen():
            try:
                yield wire.chunk(cid, created, model, {"role": "assistant"})
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
