"""Provider-agnostic browser transport.

One worker thread owns the CloakBrowser page (the sync API is not thread-safe
and a single page can't be driven concurrently). Callers submit jobs and drain
a per-job queue, so the SSE crosses threads. The transport is generic: it opens
the provider's URL, verifies auth, captures the provider's chosen response over
CDP, and feeds the bytes through the provider's accumulator — nothing here
knows about ChatGPT or Databricks.

Lifecycle: stale profile locks are cleared on boot, and on shutdown the context
is closed and any profile-scoped Chrome killed, so no orphans are left behind.
"""

import base64
import contextlib
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Any

from cloakbrowser import launch_persistent_context

from ..domain.ports import Job, Provider
from ..infra.env import env_str
from .process import clean_singleton_locks, kill_profile_chrome

log = logging.getLogger(__name__)

_DUMP = env_str("WEBLLM_PROXY_DUMP_SSE") or None


def run_login(provider: Provider, timeout_s: int = 600) -> bool:
    """Open a headed browser for a one-time login into the provider's profile.
    Generic across providers (uses `provider.authed`). Needs a display."""
    profile = provider.profile_dir
    profile.mkdir(parents=True, exist_ok=True)
    clean_singleton_locks(profile)
    print(f"[{provider.name}] opening a browser window for login (profile: {profile}) ...")
    ctx = launch_persistent_context(str(profile), headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(provider.nav_url, wait_until="domcontentloaded", timeout=60000)
        print(f"Log in to {provider.name} in the window. Waiting (up to {timeout_s} s)...")
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(4)
            try:
                if provider.authed(page):
                    print(f"[{provider.name}] logged in. Session saved to {profile}.")
                    return True
            except Exception:
                pass
            print("  ...waiting for login")
        print("Timed out waiting for login.")
        return False
    finally:
        ctx.close()
        kill_profile_chrome(str(profile))


class BrowserSession:
    def __init__(self, provider: Provider):
        self.provider = provider
        self._tasks: queue.Queue = queue.Queue()
        self.ready = False
        self.error: str | None = None
        self.last_acc = None
        # CloakBrowser/CDP objects: `cloakbrowser` itself types these `Any` (no
        # stub package); declaring them `Any` here (not inferred `None`) keeps
        # every post-boot `self._page.foo()` call honest instead of needing a
        # type-checker suppression on every single call site.
        self._ctx: Any = None
        self._page: Any = None
        self._client: Any = None
        self._active: dict | None = None
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="webllm-browser", daemon=True)

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
        kill_profile_chrome(str(self.provider.profile_dir))

    def _run(self):
        try:
            self._boot()
        except Exception as e:  # pragma: no cover
            self.error = str(e)
            log.exception("browser boot failed")
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
            job = item[1]
            try:
                self._do_send(job)
            except Exception as e:
                log.exception("send failed")
                job.out.put(("error", str(e)))
                job.out.put(None)
                self._active = None
                if not self._closing:
                    self._try_reboot()

    def _boot(self):
        p = self.provider
        profile = p.profile_dir
        profile.mkdir(parents=True, exist_ok=True)
        clean_singleton_locks(profile)
        log.info(
            "[%s] launching CloakBrowser (headless=%s) profile=%s", p.name, p.headless, profile
        )
        self._ctx = launch_persistent_context(str(profile), headless=p.headless)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._client = self._ctx.new_cdp_session(self._page)
        self._client.send("Network.enable")
        self._client.on("Network.responseReceived", self._on_resp)
        self._client.on("Network.dataReceived", self._on_data)
        self._client.on("Network.loadingFinished", self._on_fin)
        self._client.on("Network.loadingFailed", self._on_fail)
        # WebSocket capture (providers whose answer streams over a WS, e.g.
        # copilot's ChatHub) -- matched by the same `capture_match`.
        self._client.on("Network.webSocketCreated", self._on_ws_created)
        self._client.on("Network.webSocketFrameReceived", self._on_ws_frame)
        self._client.on("Network.webSocketClosed", self._on_ws_closed)
        patterns = p.fetch_patterns()
        if patterns:
            self._client.send("Fetch.enable", {"patterns": patterns})
            self._client.on("Fetch.requestPaused", self._on_fetch_paused)

        self._page.goto(p.nav_url, wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(3000)
        if not p.authed(self._page):
            raise RuntimeError(
                f"Not logged in. Run `webllm-proxy login --provider {p.name}` "
                "once (needs a display)."
            )
        p.on_ready(self._page)
        self.ready = True
        log.info("[%s] session ready (authenticated)", p.name)

    def _shutdown_browser(self):
        self.ready = False
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass

    def _try_reboot(self):
        log.warning("[%s] rebooting browser after failure", self.provider.name)
        self.ready = False
        with contextlib.suppress(Exception):
            self._ctx.close()
        kill_profile_chrome(str(self.provider.profile_dir))
        try:
            self._boot()
        except Exception as e:
            self.error = f"reboot failed: {e}"
            log.exception("reboot failed")

    # ---- CDP handlers (worker thread) ------------------------------------
    def _on_fetch_paused(self, params):
        job = self._active["job"] if self._active else None
        try:
            self.provider.on_fetch_paused(self._client, params, job)
        except Exception:
            log.exception("on_fetch_paused failed")
            with contextlib.suppress(Exception):
                self._client.send("Fetch.continueRequest", {"requestId": params["requestId"]})

    def _on_resp(self, p):
        a = self._active
        if not a or a["finished"]:
            return
        resp = p.get("response") or {}
        if self.provider.capture_match(resp.get("url", "")):
            a["rid"] = p["requestId"]
            ct = ""
            for k, v in (resp.get("headers") or {}).items():
                if k.lower() == "content-type":
                    ct = v
                    break
            a["job"].out.put(
                (
                    "meta",
                    {"status": resp.get("status", 200), "content_type": ct or "text/event-stream"},
                )
            )
            try:
                res = self._client.send("Network.streamResourceContent", {"requestId": a["rid"]})
                bd = res.get("bufferedData", "")
                if bd:
                    self._feed(a, base64.b64decode(bd).decode("utf-8", "replace"))
            except Exception:
                pass

    def _on_data(self, p):
        a = self._active
        if a and not a["finished"] and p.get("requestId") == a.get("rid"):
            d = p.get("data")
            if d:
                self._feed(a, base64.b64decode(d).decode("utf-8", "replace"))

    def _on_fin(self, p):
        a = self._active
        if a and not a["finished"] and p.get("requestId") == a.get("rid"):
            self._finish(a)

    def _on_fail(self, p):
        a = self._active
        if a and not a["finished"] and p.get("requestId") == a.get("rid"):
            a["job"].out.put(("error", f"network loadingFailed: {p.get('errorText')}"))
            self._finish(a, done_already=True)

    # ---- WebSocket capture (worker thread) -------------------------------
    def _on_ws_created(self, p):
        a = self._active
        if not a or a["finished"]:
            return
        if self.provider.capture_match(p.get("url", "")):
            a["ws"] = p.get("requestId")
            a["job"].out.put(("meta", {"status": 200, "content_type": "text/event-stream"}))

    def _on_ws_frame(self, p):
        a = self._active
        if a and not a["finished"] and p.get("requestId") == a.get("ws"):
            payload = (p.get("response") or {}).get("payloadData", "")
            if payload:
                self._feed(a, payload)

    def _on_ws_closed(self, p):
        a = self._active
        if a and not a["finished"] and p.get("requestId") == a.get("ws"):
            self._finish(a)

    def _feed(self, a, text):
        if not text:
            return
        if _DUMP:
            with contextlib.suppress(OSError), Path(_DUMP).open("a") as f:
                f.write(text)
        for ev in a["acc"].feed(text):
            if ev[0] == "done":
                self._finish(a, reason=ev[1])
                return
            a["job"].out.put(ev)

    def _finish(self, a, done_already=False, reason=None):
        if a["finished"]:
            return
        a["finished"] = True
        for ev in a["acc"].flush():
            if ev[0] != "done":
                a["job"].out.put(ev)
        self.last_acc = a["acc"]
        if not done_already:
            r = reason if reason is not None else getattr(a["acc"], "finish_reason", None)
            a["job"].out.put(("done", r))
        a["job"].out.put(None)

    # ---- public API (Flask threads) --------------------------------------
    def evaluate(self, js: str, arg=None):
        """Run `page.evaluate` on the worker thread and return the result. Only
        call from non-worker threads (Flask handlers)."""
        reply: queue.Queue = queue.Queue()
        self._tasks.put(("eval", (js, arg, reply)))
        return reply.get(timeout=30)

    def submit(
        self, payload, *, idle_cap_s: float | None = None, hard_cap_s: float | None = None
    ) -> queue.Queue:
        """Hand `payload` to the browser worker; drain the returned queue for
        events (terminated by `None`). `idle_cap_s`/`hard_cap_s` override the
        `Job` defaults (an interactive chat turn) -- pass longer caps for a
        long-running job (research) sharing this same browser."""
        overrides = {}
        if idle_cap_s is not None:
            overrides["idle_cap_s"] = idle_cap_s
        if hard_cap_s is not None:
            overrides["hard_cap_s"] = hard_cap_s
        job = Job(payload, **overrides)
        self._tasks.put(("send", job))
        return job.out

    # ---- worker-thread send ----------------------------------------------
    def _do_send(self, job: Job):
        self._active = {
            "job": job,
            "acc": self.provider.make_accumulator(),
            "rid": None,
            "ws": None,
            "finished": False,
        }
        a = self._active
        try:
            self.provider.trigger(self._page, job)
        except Exception as e:
            job.out.put(("error", str(e)))
            job.out.put(None)
            self._active = None
            return

        start = time.time()
        idle_deadline = start + job.idle_cap_s
        hard_deadline = start + job.hard_cap_s
        while not a["finished"]:
            self._page.wait_for_timeout(50)
            now = time.time()
            if a["rid"] is not None:
                # Once the response starts, only `hard_deadline` below still
                # applies -- this reset doesn't gate anything further (the
                # check that reads `idle_deadline` requires `rid is None`,
                # which is no longer true). A long job (research) relies
                # entirely on `hard_cap_s` once bytes start flowing; a real
                # post-first-byte stall timeout is a documented gap, not
                # implemented here (see docs/refactor/PROGRESS.md).
                idle_deadline = now + 40
            if now > hard_deadline:
                job.out.put(("error", "timeout (hard cap)"))
                job.out.put(None)
                a["finished"] = True
                break
            if a["rid"] is None and a.get("ws") is None and now > idle_deadline:
                job.out.put(("error", "no response started"))
                job.out.put(None)
                a["finished"] = True
                break
        self._active = None
