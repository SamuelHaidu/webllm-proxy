"""ChatGPT web provider: OpenAI-compatible surface backed by a logged-in
chatgpt.com session. Tools are emulated (`tools.py`) and reasoning maps onto
ChatGPT web's `thinking_effort`; the model is forced by rewriting the
`f/conversation` request body via CDP `Fetch`.
"""

import base64
import json
import logging
from pathlib import Path

from ...domain.ports import Accumulator, Provider
from . import config
from .sse import StreamAccumulator

log = logging.getLogger(__name__)


_MODELS_JS = """async () => {
  const s = await (await fetch('/api/auth/session')).json();
  const t = s.accessToken;
  const r = await fetch('/backend-api/models', {headers: {'Authorization': 'Bearer ' + t}});
  if (!r.ok) return {error: 'models ' + r.status};
  return await r.json();
}"""


def _effort_values(entries) -> set[str]:
    """The allowed `thinking_effort` strings from one model's `thinking_efforts`
    list (each entry is either a bare string or a dict with the value under one
    of a few possible keys, per the real `/backend-api/models` response)."""
    vals: set[str] = set()
    for e in entries:
        if isinstance(e, str):
            vals.add(e)
        elif isinstance(e, dict):
            v = e.get("thinking_effort") or e.get("effort") or e.get("id") or e.get("value")
            if isinstance(v, str):
                vals.add(v)
    return vals


def _model_effort_entry(m) -> tuple[str, set[str]] | None:
    """(slug, allowed efforts) for one `/backend-api/models` entry, or None if
    it doesn't advertise configurable thinking effort."""
    if not (isinstance(m, dict) and m.get("configurable_thinking_effort") and m.get("slug")):
        return None
    vals = _effort_values(m.get("thinking_efforts") or [])
    return (m["slug"], vals) if vals else None


def _apply_overrides(body, forced_model, forced_effort, effort_support) -> bool:
    """Mutate a parsed `f/conversation` body in place: force the model and/or
    inject the root `thinking_effort`. Effort is only injected when the effective
    model advertises `configurable_thinking_effort` and the value is allowed (so
    it's a safe no-op otherwise). Returns True iff the body changed."""
    changed = False
    if forced_model:
        body["model"] = forced_model
        changed = True
    if forced_effort:
        allowed = effort_support.get(body.get("model"))
        if allowed and forced_effort in allowed:
            body["thinking_effort"] = forced_effort
            changed = True
    return changed


class ChatGptProvider(Provider):
    name = "chatgpt"

    def __init__(self, host: str | None = None, port: int | None = None):
        self._host = host or config.HOST
        self._port = port or config.PORT
        self._effort_support: dict[str, set[str]] = {}

    # ---- config ----------------------------------------------------------
    @property
    def profile_dir(self) -> Path:
        return config.PROFILE_DIR

    @property
    def nav_url(self) -> str:
        return config.CHATGPT_URL + "/"

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
    def fetch_patterns(self):
        # Intercept the send so we can force model / thinking_effort in the body.
        return [{"urlPattern": "*/backend-api/f/conversation*", "requestStage": "Request"}]

    def on_fetch_paused(self, client, params, job):
        args = {"requestId": params["requestId"]}
        try:
            post = (params.get("request") or {}).get("postData")
            fm = getattr(job.payload, "model", None) if job else None
            fe = getattr(job.payload, "effort", None) if job else None
            if post and (fm or fe):
                b = json.loads(post)
                if _apply_overrides(b, fm, fe, self._effort_support):
                    args["postData"] = base64.b64encode(json.dumps(b).encode()).decode()
        except Exception:
            pass
        client.send("Fetch.continueRequest", args)

    def authed(self, page) -> bool:
        try:
            r = page.request.get(config.CHATGPT_URL + "/api/auth/session")
            return bool(r.ok and r.json().get("accessToken"))
        except Exception:
            return False

    def on_ready(self, page):
        self._effort_support = self._load_effort_support(page)
        if self._effort_support:
            log.info(
                "thinking_effort configurable models: %s",
                {k: sorted(v) for k, v in self._effort_support.items()},
            )

    def _load_effort_support(self, page) -> dict[str, set[str]]:
        try:
            data = page.evaluate(_MODELS_JS)
        except Exception:
            return {}
        if not isinstance(data, dict):
            return {}
        entries = (_model_effort_entry(m) for m in data.get("models") or [])
        return dict(e for e in entries if e is not None)

    def capture_match(self, url: str) -> bool:
        return url.split("?", maxsplit=1)[0].endswith("/f/conversation")

    def trigger(self, page, job):
        turn = job.payload
        if turn.new_conversation:
            page.goto(config.CHATGPT_URL + "/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)
        comp = self._find_composer(page)
        if not comp:
            raise RuntimeError("composer not found")
        comp.click()
        page.wait_for_timeout(150)
        page.keyboard.insert_text(turn.message)
        page.wait_for_timeout(150)
        page.keyboard.press("Enter")

    @staticmethod
    def _find_composer(page):
        for sel in ("#prompt-textarea", 'div[contenteditable="true"]', "textarea"):
            loc = page.locator(sel).first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                pass
        return None

    def make_accumulator(self) -> Accumulator:
        return StreamAccumulator()

    # ---- runtime helpers used by routes ----------------------------------
    def list_models(self, session):
        return session.evaluate(_MODELS_JS)

    # ---- HTTP surface ----------------------------------------------------
    def register_routes(self, app, session):
        # Lazy: http/openai_routes.py needs both providers' config (it hosts
        # databricks' Azure channel too), so importing it eagerly here would
        # make selecting chatgpt also import databricks' config -- same
        # reasoning as providers/__init__.py's lazy provider imports.
        from ...http.openai_routes import register_chatgpt

        register_chatgpt(app, session, self)

    def banner(self, host, port):
        return [
            f"  GET  http://{host}:{port}/v1/models",
            f"  POST http://{host}:{port}/v1/chat/completions",
        ]
