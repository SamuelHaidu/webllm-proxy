"""Browser-backed ChatGPT session.

All CloakBrowser/Playwright calls run on ONE worker thread (the sync API is not
thread-safe and a single page can't be driven concurrently). Flask handlers
submit jobs and drain a per-job queue, so streaming crosses threads.

Send mechanism (verified — see docs/discovery/):
- Persistent, already-logged-in CloakBrowser session (headless). CloakBrowser
  is a stealth Chromium that passes Cloudflare Turnstile.
- Type the prompt into the composer + Enter: the frontend mints the
  sentinel/Turnstile/proof-of-work tokens and issues the real
  `POST /backend-api/f/conversation`.
- Capture the SSE over CDP (`Network.streamResourceContent` streams the body
  incrementally); parse the `v1` delta encoding into content/reasoning.
- Select the model by rewriting the request body's `model` via the CDP `Fetch`
  domain. (A `window.fetch` hook does NOT work — the app doesn't send through
  page fetch.)

Lifecycle: stale profile locks are cleared on boot (so a prior crash can't
block launch), and the browser is closed + any profile-scoped Chrome killed on
shutdown, so no orphan processes are left behind.
"""
import base64
import json
import logging
import os
import queue
import signal
import threading
import time
from pathlib import Path

from cloakbrowser import launch_persistent_context

from . import config
from .sse import StreamAccumulator

log = logging.getLogger(__name__)


# ---- process/lock hygiene -------------------------------------------------
def clean_singleton_locks(profile: Path) -> None:
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.debug("could not remove %s: %s", name, e)


def _profile_chrome_pids(profile: str) -> list[int]:
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.exists():
        return pids
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "chrome" in cmd and profile in cmd:
            pids.append(int(d.name))
    return pids


def kill_profile_chrome(profile: str) -> int:
    pids = _profile_chrome_pids(profile)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if pids:
        time.sleep(1.0)
        for pid in _profile_chrome_pids(profile):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return len(pids)


def _is_conv(url: str) -> bool:
    return url.split("?")[0].endswith("/f/conversation")


_MODELS_JS = """async () => {
  const s = await (await fetch('/api/auth/session')).json();
  const t = s.accessToken;
  const r = await fetch('/backend-api/models', {headers: {'Authorization': 'Bearer ' + t}});
  if (!r.ok) return {error: 'models ' + r.status};
  return await r.json();
}"""


# ---- headed one-time login ------------------------------------------------
def run_login(timeout_s: int = 600) -> bool:
    """Open a headed browser for a one-time ChatGPT login into the profile.
    Returns True once authenticated. Needs a display."""
    profile = config.PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)
    clean_singleton_locks(profile)
    print(f"Opening a browser window for login (profile: {profile}) ...")
    ctx = launch_persistent_context(str(profile), headless=False)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.CHATGPT_URL, wait_until="domcontentloaded", timeout=60000)
        print("Log in to ChatGPT in the window. Waiting (up to %d s)..." % timeout_s)
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            time.sleep(4)
            try:
                r = page.request.get(config.CHATGPT_URL + "/api/auth/session")
                if r.ok and r.json().get("accessToken"):
                    email = (r.json().get("user") or {}).get("email", "?")
                    print(f"Logged in as {email}. Session saved to {profile}.")
                    return True
            except Exception:
                pass
            print("  ...waiting for login")
        print("Timed out waiting for login.")
        return False
    finally:
        ctx.close()
        kill_profile_chrome(str(profile))


class Job:
    def __init__(self, message: str, model: str | None, new_conversation: bool):
        self.message = message
        self.model = model
        self.new_conversation = new_conversation
        self.out: queue.Queue = queue.Queue()


class BrowserSession:
    def __init__(self):
        self._tasks: queue.Queue = queue.Queue()
        self.ready = False
        self.error: str | None = None
        self.model_slug_last: str | None = None
        self._ctx = None
        self._page = None
        self._client = None
        self._active: dict | None = None
        self._forced_model: str | None = None
        self._closing = False
        self._thread = threading.Thread(target=self._run, name="cgp-browser", daemon=True)

    # ---- lifecycle --------------------------------------------------------
    def start(self):
        self._thread.start()

    def wait_ready(self, timeout: float = 90.0) -> bool:
        t0 = time.time()
        while not self.ready and self.error is None and time.time() - t0 < timeout:
            time.sleep(0.25)
        return self.ready

    def close(self, join_timeout: float = 8.0):
        """Ask the worker to shut the browser down cleanly, then make sure no
        profile-scoped Chrome is left running. Safe to call from any thread."""
        if self._closing:
            return
        self._closing = True
        self._tasks.put(("close", None))
        self._thread.join(timeout=join_timeout)
        kill_profile_chrome(str(config.PROFILE_DIR))

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
            if item[0] == "models":
                try:
                    item[1].put(self._page.evaluate(_MODELS_JS))
                except Exception as e:
                    item[1].put({"error": str(e)})
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
        profile = config.PROFILE_DIR
        profile.mkdir(parents=True, exist_ok=True)
        clean_singleton_locks(profile)
        log.info("launching CloakBrowser (headless=%s) profile=%s", config.HEADLESS, profile)
        self._ctx = launch_persistent_context(str(profile), headless=config.HEADLESS)
        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._client = self._ctx.new_cdp_session(self._page)
        self._client.send("Network.enable")
        self._client.on("Network.responseReceived", self._on_resp)
        self._client.on("Network.dataReceived", self._on_data)
        self._client.on("Network.loadingFinished", self._on_fin)
        self._client.on("Network.loadingFailed", self._on_fail)
        self._client.send("Fetch.enable", {"patterns": [
            {"urlPattern": "*/backend-api/f/conversation*", "requestStage": "Request"},
        ]})
        self._client.on("Fetch.requestPaused", self._on_fetch_paused)

        self._page.goto(config.CHATGPT_URL + "/", wait_until="domcontentloaded", timeout=60000)
        self._page.wait_for_timeout(3000)
        if not self._authed():
            raise RuntimeError(
                "Not logged in. Run `chatgpt-proxy login` once (needs a display).")
        self.ready = True
        log.info("session ready (authenticated)")

    def _authed(self) -> bool:
        try:
            r = self._page.request.get(config.CHATGPT_URL + "/api/auth/session")
            return bool(r.ok and r.json().get("accessToken"))
        except Exception:
            return False

    def _shutdown_browser(self):
        self.ready = False
        try:
            if self._ctx is not None:
                self._ctx.close()
        except Exception:
            pass

    def _try_reboot(self):
        log.warning("rebooting browser after failure")
        self.ready = False
        try:
            self._ctx.close()
        except Exception:
            pass
        kill_profile_chrome(str(config.PROFILE_DIR))
        try:
            self._boot()
        except Exception as e:
            self.error = "reboot failed: %s" % e
            log.exception("reboot failed")

    # ---- CDP handlers (worker thread) ------------------------------------
    def _on_fetch_paused(self, p):
        fid = p["requestId"]
        args = {"requestId": fid}
        try:
            post = (p.get("request") or {}).get("postData")
            if post and self._forced_model:
                b = json.loads(post)
                b["model"] = self._forced_model
                args["postData"] = base64.b64encode(json.dumps(b).encode()).decode()
        except Exception:
            pass
        try:
            self._client.send("Fetch.continueRequest", args)
        except Exception:
            log.exception("continueRequest failed")

    def _on_resp(self, p):
        a = self._active
        if not a or a["finished"]:
            return
        if _is_conv((p.get("response") or {}).get("url", "")):
            a["rid"] = p["requestId"]
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
            a["job"].out.put(("error", "network loadingFailed: %s" % p.get("errorText")))
            self._finish(a, done_already=True)

    def _feed(self, a, text):
        for ev in a["acc"].feed(text):
            if ev[0] == "done":
                self._finish(a)
                return
            a["job"].out.put(ev)

    def _finish(self, a, done_already=False):
        if a["finished"]:
            return
        a["finished"] = True
        for ev in a["acc"].flush():
            if ev[0] != "done":
                a["job"].out.put(ev)
        self.model_slug_last = a["acc"].parser.model_slug
        if not done_already:
            a["job"].out.put(("done", a["acc"].parser.finish_reason or "stop"))
        a["job"].out.put(None)

    # ---- public API (Flask threads) --------------------------------------
    def list_models(self):
        reply: queue.Queue = queue.Queue()
        self._tasks.put(("models", reply))
        return reply.get(timeout=30)

    def submit(self, message: str, model: str | None, new_conversation: bool) -> queue.Queue:
        job = Job(message, model, new_conversation)
        self._tasks.put(("send", job))
        return job.out

    # ---- worker-thread send ----------------------------------------------
    def _find_composer(self):
        for sel in ("#prompt-textarea", 'div[contenteditable="true"]', "textarea"):
            loc = self._page.locator(sel).first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                pass
        return None

    def _do_send(self, job: Job):
        page = self._page
        self._forced_model = job.model or None

        if job.new_conversation:
            page.goto(config.CHATGPT_URL + "/", wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1200)

        comp = self._find_composer()
        if not comp:
            job.out.put(("error", "composer not found")); job.out.put(None); return

        self._active = {"job": job, "acc": StreamAccumulator(), "rid": None, "finished": False}
        comp.click()
        page.wait_for_timeout(150)
        page.keyboard.insert_text(job.message)
        page.wait_for_timeout(150)
        page.keyboard.press("Enter")

        a = self._active
        start = time.time()
        idle_deadline = start + 45
        hard_deadline = start + 300
        while not a["finished"]:
            page.wait_for_timeout(50)
            now = time.time()
            if a["rid"] is not None:
                idle_deadline = now + 40
            if now > hard_deadline:
                if not a["finished"]:
                    job.out.put(("error", "timeout (hard cap)")); job.out.put(None)
                    a["finished"] = True
                break
            if a["rid"] is None and now > idle_deadline:
                job.out.put(("error", "no response started (sentinel/turnstile blocked?)"))
                job.out.put(None)
                a["finished"] = True
                break
        self._active = None
