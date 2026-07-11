"""Databricks Genie / `llmproxy` provider.

The Genie-code channel `POST /ajax-api/2.0/conversation/llmproxy/` is a thin
passthrough to the native Anthropic Messages API on AWS Bedrock (Claude Sonnet
4.5, native tool_use, extended thinking). There's no anti-bot token-minting, so
we just issue the fetch **in-page** (the httpOnly session cookie auto-attaches;
the CSRF token, read from `/auth/session/info`, never leaves the browser) and
forward the native Anthropic SSE through unchanged — the client (`pi`, or any
Anthropic SDK) parses it directly.
"""

import logging
from pathlib import Path

from ...domain.ports import Accumulator, PassthroughAccumulator, Provider
from . import config

log = logging.getLogger(__name__)

_SESSION_JS = (
    "async () => { try { const r = await fetch('/auth/session/info',"
    "{credentials:'include'}); if(!r.ok) return null; "
    "return (await r.json()).userId || null; } catch(e){ return null; } }"
)

# In-page: read a fresh CSRF token, POST the request to `arg.path` (the llmproxy
# sub-path for the chosen channel: Anthropic `llmproxy/` or Azure
# `proxy/chat/completions`), and drain the body so the response completes promptly
# (Network.loadingFinished fires) instead of lingering open. The org id is passed
# in from config (authoritative) rather than read from location.search, which the
# workspace SPA drops after routing (an empty x-databricks-org-id -> 400).
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


class DatabricksProvider(Provider):
    name = "databricks"

    def __init__(self, host: str | None = None, port: int | None = None):
        self._host = host or config.HOST
        self._port = port or config.PORT

    # ---- config ----------------------------------------------------------
    @property
    def profile_dir(self) -> Path:
        return config.PROFILE_DIR

    @property
    def nav_url(self) -> str:
        if not config.WORKSPACE_URL:
            raise RuntimeError("DATABRICKS_PROXY_URL is not set (workspace URL with ?o=).")
        return config.WORKSPACE_URL

    @property
    def headless(self) -> bool:
        return config.HEADLESS

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # ---- browser hooks ---------------------------------------------------
    def authed(self, page) -> bool:
        try:
            return bool(page.evaluate(_SESSION_JS))
        except Exception:
            return False

    def capture_match(self, url: str) -> bool:
        path = url.split("?", maxsplit=1)[0]
        return path.endswith(config.LLMPROXY_PATH) or path.endswith(config.CHAT_COMPLETIONS_PATH)

    def trigger(self, page, job):
        # job.payload is {"path": <llmproxy sub-path>, "body": <request body>}:
        # the route picks the channel (Anthropic `llmproxy/` or Azure
        # `proxy/chat/completions`). Pass the org id from config so it survives the
        # SPA dropping ?o=.
        page.evaluate(
            _START_JS,
            {"path": job.payload["path"], "body": job.payload["body"], "org": config.org_id()},
        )

    def make_accumulator(self) -> Accumulator:
        return PassthroughAccumulator()

    # ---- HTTP surface ----------------------------------------------------
    def register_routes(self, app, session):
        # Lazy: see the matching comment in providers/chatgpt/__init__.py.
        from ...http.anthropic_routes import register_databricks
        from ...http.openai_routes import register_databricks_openai

        register_databricks(app, session, self)
        register_databricks_openai(app, session, self)

    def banner(self, host, port):
        return [
            f"  GET  http://{host}:{port}/v1/models",
            f"  POST http://{host}:{port}/v1/messages          (Anthropic; Claude)",
            f"  POST http://{host}:{port}/v1/messages/count_tokens",
            f"  POST http://{host}:{port}/v1/chat/completions   (OpenAI; GPT-4.1 Azure)",
        ]
