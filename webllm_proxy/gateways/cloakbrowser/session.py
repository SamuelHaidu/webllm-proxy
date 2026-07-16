"""One worker thread owns the CloakBrowser page (the sync API is not thread-safe
and a single page can't be driven concurrently). Providers submit turns through
`run_turn` and drain a per-turn queue, so the SSE crosses threads.

Inversion of control: the session knows nothing about ChatGPT/Databricks/Copilot.
Each turn supplies its own `trigger` (start the request), `capture_url` (which
response to capture), `parse` (accumulator: `feed(text)->events`/`flush()`), and
optional `fetch_rewrite` (mutate the intercepted outgoing POST body).

Lifecycle: stale profile locks cleared on boot; on shutdown the context is
closed and any profile-scoped Chrome killed, so no orphans are left behind.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import queue
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cloakbrowser import launch_persistent_context

from ...utils.env import env_str
from ...utils.process import clean_singleton_locks, kill_profile_chrome

log = logging.getLogger(__name__)

_DUMP = env_str("WEBLLM_PROXY_DUMP_SSE") or None


class _Turn:
    """One in-flight browser turn's state + its event queue."""

    def __init__(self, spec: dict):
        self.spec = spec  # trigger/capture_url/parse/fetch_rewrite/caps
        self.out: queue.Queue = queue.Queue()
        self.parse = spec["parse"]
        self.rid: str | None = None
        self.ws: str | None = None
        self.finished = False


def run_login(
    *,
    name: str,
    nav_url: str,
    profile_dir: Path,
    authed: Callable[[Any], bool],
    steer: Callable[[Any], None] | None = None,
    timeout_s: int = 600,
) -> bool:
    """Open a headed browser for a one-time login into the provider's profile.

    `steer`, if given, runs once per poll after a failed `authed()` check: a
    provider can use it to nudge the browser back toward its app surface when a
    login lands on an off-app page (see copilot.login_steer)."""
    profile_dir.mkdir(parents=True, exist_ok=True)
    clean_singleton_locks(profile_dir)
    print(f"[{name}] opening a browser window for login (profile: {profile_dir}) ...")
    ctx = launch_persistent_context(str(profile_dir), headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(nav_url, wait_until="domcontentloaded", timeout=60000)
        print(f"Log in to {name} in the window. Waiting (up to {timeout_s} s)...")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(4)
            try:
                if authed(page):
                    print(f"[{name}] logged in. Session saved to {profile_dir}.")
                    return True
            except Exception:
                pass
            if steer is not None:
                with contextlib.suppress(Exception):
                    steer(page)
            print("  ...waiting for login")
        print("Timed out waiting for login.")
        return False
    finally:
        ctx.close()
        kill_profile_chrome(str(profile_dir))


class BrowserSession:
    def __init__(
        self,
        *,
        name: str,
        nav_url: str,
        profile_dir: Path,
        headless: bool,
        authed: Callable[[Any], bool],
        fetch_patterns: list[dict] | None = None,
        extension_paths: list[str] | None = None,
    ):
        self.name = name
        self.nav_url = nav_url
        self.profile_dir = profile_dir
        self.headless = headless
        self._authed = authed
        self._fetch_patterns = fetch_patterns or []
        self._extension_paths = extension_paths or None
        self._tasks: queue.Queue = queue.Queue()
        self.ready = False
        self.error: str | None = None
        self._ctx: Any = None
        self._page: Any = None
        self._client: Any = None
        self._active: _Turn | None = None
        self._closing = False
        self._thread = threading.Thread(
            target=self._run, name=f"webllm-browser-{name}", daemon=True
        )

    # ---- lifecycle --------------------------------------------------------
    def start(self):
        self._thread.start()

    def wait_ready(self, timeout: float = 90.0) -> bool:
        t0 = time.time()
        while not self.ready and self.error is None and time.time() - t0 < timeout:
            time.sleep(0.25)
        return self.ready

    def close(self, join_timeout: float = 8.0):
        if self._closing:
            return
        self._closing = True
        self._tasks.put(("close", None))
        self._thread.join(timeout=join_timeout)
        kill_profile_chrome(str(self.profile_dir))

    def _run(self):
        try:
            self._boot()
        except Exception as e:  # pragma: no cover
            self.error = str(e)
            log.exception("[%s] browser boot failed", self.name)
            return
        while True:
            item = self._tasks.get()
            if item is None or item[0] == "close":
                self._shutdown_browser()
                return
            if item[0] == "eval":
                js, arg, reply = item[1]
                try:
                    reply.put(
                        self._page.evaluate(js, arg) if arg is not None else self._page.evaluate(js)
                    )
                except Exception as e:
                    reply.put({"error": str(e)})
                continue
            turn = item[1]
            try:
                self._do_turn(turn)
            except Exception as e:
                log.exception("[%s] turn failed", self.name)
                turn.out.put(("error", str(e)))
                turn.out.put(None)
                self._active = None
                if not self._closing:
                    self._try_reboot()

    def _boot(self):
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        clean_singleton_locks(self.profile_dir)
        log.info(
            "[%s] launching CloakBrowser (headless=%s) profile=%s",
            self.name,
            self.headless,
            self.profile_dir,
        )
        self._ctx = launch_persistent_context(
            str(self.profile_dir),
            headless=self.headless,
            extension_paths=self._extension_paths,
        )
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._client = self._ctx.new_cdp_session(self._page)
        self._client.send("Network.enable")
        self._client.on("Network.responseReceived", self._on_resp)
        self._client.on("Network.dataReceived", self._on_data)
        self._client.on("Network.loadingFinished", self._on_fin)
        self._client.on("Network.loadingFailed", self._on_fail)
        self._client.on("Network.webSocketCreated", self._on_ws_created)
        self._client.on("Network.webSocketFrameReceived", self._on_ws_frame)
        self._client.on("Network.webSocketClosed", self._on_ws_closed)
        if self._fetch_patterns:
            self._client.send("Fetch.enable", {"patterns": self._fetch_patterns})
            self._client.on("Fetch.requestPaused", self._on_fetch_paused)
        self._page.goto(self.nav_url, wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(3000)
        if not self._authed(self._page):
            raise RuntimeError(
                f"Not logged in. Run `webllm-proxy login --provider {self.name}` "
                "once (needs a display)."
            )
        self.ready = True
        log.info("[%s] session ready (authenticated)", self.name)

    def _shutdown_browser(self):
        self.ready = False
        with contextlib.suppress(Exception):
            if self._ctx is not None:
                self._ctx.close()

    def _try_reboot(self):
        log.warning("[%s] rebooting browser after failure", self.name)
        self.ready = False
        with contextlib.suppress(Exception):
            self._ctx.close()
        kill_profile_chrome(str(self.profile_dir))
        try:
            self._boot()
        except Exception as e:
            self.error = f"reboot failed: {e}"
            log.exception("[%s] reboot failed", self.name)

    # ---- CDP handlers (worker thread) ------------------------------------
    def _on_fetch_paused(self, params):
        args = {"requestId": params["requestId"]}
        turn = self._active
        rewrite = turn.spec.get("fetch_rewrite") if turn else None
        if rewrite:
            try:
                post = (params.get("request") or {}).get("postData")
                if post:
                    new_post = rewrite(post)
                    if new_post is not None and new_post != post:
                        args["postData"] = base64.b64encode(new_post.encode()).decode()
            except Exception:
                log.exception("[%s] fetch_rewrite failed", self.name)
        with contextlib.suppress(Exception):
            self._client.send("Fetch.continueRequest", args)

    def _capture_match(self, url: str) -> bool:
        turn = self._active
        return bool(turn) and bool(turn.spec["capture_url"](url))

    def _on_resp(self, p):
        a = self._active
        if not a or a.finished:
            return
        resp = p.get("response") or {}
        if self._capture_match(resp.get("url", "")):
            a.rid = p["requestId"]
            ct = ""
            for k, v in (resp.get("headers") or {}).items():
                if k.lower() == "content-type":
                    ct = v
                    break
            a.out.put(
                (
                    "meta",
                    {"status": resp.get("status", 200), "content_type": ct or "text/event-stream"},
                )
            )
            try:
                res = self._client.send("Network.streamResourceContent", {"requestId": a.rid})
                bd = res.get("bufferedData", "")
                if bd:
                    self._feed(a, base64.b64decode(bd).decode("utf-8", "replace"))
            except Exception:
                pass

    def _on_data(self, p):
        a = self._active
        if a and not a.finished and p.get("requestId") == a.rid:
            d = p.get("data")
            if d:
                self._feed(a, base64.b64decode(d).decode("utf-8", "replace"))

    def _on_fin(self, p):
        a = self._active
        if a and not a.finished and p.get("requestId") == a.rid:
            self._finish(a)

    def _on_fail(self, p):
        a = self._active
        if a and not a.finished and p.get("requestId") == a.rid:
            a.out.put(("error", f"network loadingFailed: {p.get('errorText')}"))
            self._finish(a, done_already=True)

    # ---- WebSocket capture -----------------------------------------------
    def _on_ws_created(self, p):
        a = self._active
        if not a or a.finished:
            return
        if self._capture_match(p.get("url", "")):
            a.ws = p.get("requestId")
            a.out.put(("meta", {"status": 200, "content_type": "text/event-stream"}))

    def _on_ws_frame(self, p):
        a = self._active
        if a and not a.finished and p.get("requestId") == a.ws:
            payload = (p.get("response") or {}).get("payloadData", "")
            if payload:
                self._feed(a, payload)

    def _on_ws_closed(self, p):
        a = self._active
        if a and not a.finished and p.get("requestId") == a.ws:
            self._finish(a)

    def _feed(self, a: _Turn, text: str):
        if not text:
            return
        if _DUMP:
            with contextlib.suppress(OSError), Path(_DUMP).open("a", encoding="utf-8") as f:
                f.write(text)
        for ev in a.parse.feed(text):
            if ev[0] == "done":
                self._finish(a, reason=ev[1])
                return
            a.out.put(ev)

    def _finish(self, a: _Turn, done_already=False, reason=None):
        if a.finished:
            return
        a.finished = True
        for ev in a.parse.flush():
            if ev[0] != "done":
                a.out.put(ev)
        if not done_already:
            r = reason if reason is not None else getattr(a.parse, "finish_reason", None)
            a.out.put(("done", r))
        a.out.put(None)

    # ---- public API (Flask threads) --------------------------------------
    def evaluate(self, js: str, arg=None):
        """Run `page.evaluate` on the worker thread and return the result."""
        reply: queue.Queue = queue.Queue()
        self._tasks.put(("eval", (js, arg, reply)))
        return reply.get(timeout=30)

    def run_turn(
        self,
        *,
        trigger: Callable[[Any], None],
        capture_url: Callable[[str], bool],
        parse,
        fetch_rewrite: Callable[[str], str | None] | None = None,
        idle_cap_s: float = 45.0,
        hard_cap_s: float = 300.0,
    ) -> queue.Queue:
        """Submit one turn to the browser worker; drain the returned queue for
        events (terminated by `None`)."""
        turn = _Turn(
            {
                "trigger": trigger,
                "capture_url": capture_url,
                "parse": parse,
                "fetch_rewrite": fetch_rewrite,
                "idle_cap_s": idle_cap_s,
                "hard_cap_s": hard_cap_s,
            }
        )
        self._tasks.put(("turn", turn))
        return turn.out

    # ---- worker-thread turn ----------------------------------------------
    def _do_turn(self, turn: _Turn):
        self._active = turn
        try:
            turn.spec["trigger"](self._page)
        except Exception as e:
            turn.out.put(("error", str(e)))
            turn.out.put(None)
            self._active = None
            return

        start = time.time()
        idle_deadline = start + turn.spec["idle_cap_s"]
        hard_deadline = start + turn.spec["hard_cap_s"]
        while not turn.finished:
            self._page.wait_for_timeout(50)
            now = time.time()
            if turn.rid is not None:
                idle_deadline = now + 40
            if now > hard_deadline:
                turn.out.put(("error", "timeout (hard cap)"))
                turn.out.put(None)
                turn.finished = True
                break
            if turn.rid is None and turn.ws is None and now > idle_deadline:
                turn.out.put(("error", "no response started"))
                turn.out.put(None)
                turn.finished = True
                break
        self._active = None
