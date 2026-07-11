"""Argparse CLI, imported by the thin `__main__.py` entry point.

  webllm-proxy serve    --provider chatgpt|databricks   run the API server (default)
  webllm-proxy login    --provider chatgpt|databricks   one-time headed login (needs a display)
  webllm-proxy install                                  pre-download the stealth browser binary
  webllm-proxy research "<query>"                       submit + poll a research job
                                                         (needs `serve` running)

The provider may also be set with WEBLLM_PROXY_PROVIDER (default: chatgpt).
"""

import argparse
import atexit
import contextlib
import json
import signal
import sys
import time
import urllib.error
import urllib.request

from ._version import __version__
from .infra import env
from .infra.logging import configure_logging
from .providers import PROVIDERS, get_provider

DEFAULT_PROVIDER = env.env_str("WEBLLM_PROXY_PROVIDER", "chatgpt")

_CORPORATE_INSTALL_HELP = """
Behind a corporate proxy / TLS-inspecting gateway (e.g. Netskope) that blocks
this download? Any ONE of:
  - Pre-stage the binary on this machine and set CLOAKBROWSER_BINARY_PATH.
  - Point CLOAKBROWSER_DOWNLOAD_URL at an internal mirror.
  - Build an offline bundle on a connected machine (`uv run poe bundle`) and
    install it here with install_offline.sh / install_offline.ps1.
  - Run the Docker fallback image `cloakhq/cloakbrowser` instead.
Also set HTTPS_PROXY/HTTP_PROXY and REQUESTS_CA_BUNDLE/SSL_CERT_FILE (your
corporate root CA) if the gateway does TLS inspection.
""".strip()


def _register_shutdown(handler) -> None:
    """Register `handler` on every shutdown signal that exists on this platform
    (SIGTERM/SIGINT everywhere, SIGBREAK on Windows) -- `signal.signal` raises on
    an unsupported signal or off the main thread, so each registration is
    best-effort."""
    for sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, handler)


def _serve(name: str, host: str | None, port: int | None) -> int:
    from .server import build_app
    from .transport.browser import BrowserSession

    provider = get_provider(name, host, port)
    try:
        _ = provider.nav_url  # surface missing config (e.g. workspace URL) early
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    session = BrowserSession(provider)
    session.start()
    print(
        f"[{provider.name}] booting browser "
        f"(profile={provider.profile_dir}, headless={provider.headless}) ..."
    )
    if not session.wait_ready(90):
        print("FATAL:", session.error or "browser did not become ready in time", file=sys.stderr)
        session.close()
        return 1

    app = build_app(session, provider)
    atexit.register(session.close)  # belt-and-suspenders; close() is idempotent

    def _shutdown(_signum, _frame):
        print("\nShutting down ...")
        session.close()
        sys.exit(0)

    _register_shutdown(_shutdown)

    print(f"[{provider.name}] ready on http://{provider.host}:{provider.port}")
    for line in provider.banner(provider.host, provider.port):
        print(line)
    try:
        app.run(
            host=provider.host, port=provider.port, threaded=True, debug=False, use_reloader=False
        )
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    return 0


def _login(name: str) -> int:
    from .transport.browser import run_login

    provider = get_provider(name)
    try:
        _ = provider.nav_url
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    return 0 if run_login(provider) else 1


def _install() -> int:
    from cloakbrowser import ensure_binary

    print("Downloading the stealth browser binary (first run only) ...")
    try:
        path = ensure_binary()
    except Exception as e:
        print(f"FATAL: could not obtain the browser binary: {e}", file=sys.stderr)
        print(_CORPORATE_INSTALL_HELP, file=sys.stderr)
        return 1
    print("Ready:", path)
    return 0


def _research_request(url: str, *, data: bytes | None = None, method: str | None = None) -> dict:
    req = urllib.request.Request(
        url, data=data, method=method, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _research(query: str, host: str, port: int, timeout_s: float = 1800.0) -> int:
    """Client for a `serve`d proxy's async research job API: submit, poll,
    print progress notes as they appear, then the final markdown report."""
    base = f"http://{host}:{port}"
    try:
        job = _research_request(f"{base}/v1/research", data=json.dumps({"query": query}).encode())
    except urllib.error.URLError as e:
        print(
            f"FATAL: could not reach {base} ({e}). Is `webllm-proxy serve` running?",
            file=sys.stderr,
        )
        return 2

    job_id = job["id"]
    print(f"[research] submitted job {job_id}: {query!r}")
    seen_progress = 0
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        job = _research_request(f"{base}/v1/research/{job_id}")
        for note in job["progress"][seen_progress:]:
            print(f"[research]   ... {note}")
        seen_progress = len(job["progress"])
        if job["status"] == "succeeded":
            print("\n" + job["report"])
            return 0
        if job["status"] == "failed":
            print(f"FATAL: research job failed: {job['error']}", file=sys.stderr)
            return 1
        time.sleep(2.0)
    print("FATAL: timed out waiting for the research job", file=sys.stderr)
    return 1


def main(argv=None) -> int:
    configure_logging()
    p = argparse.ArgumentParser(
        prog="webllm-proxy",
        description="Browser-backed local API bridges over login-only web LLMs "
        "(ChatGPT web, Databricks Genie/llmproxy).",
    )
    p.add_argument("--version", action="version", version=__version__)

    def _add_provider(sp):
        sp.add_argument(
            "-p",
            "--provider",
            choices=PROVIDERS,
            default=DEFAULT_PROVIDER,
            help="which backend (default: %(default)s)",
        )

    sub = p.add_subparsers(dest="cmd")
    sp_serve = sub.add_parser("serve", help="run the API server (default)")
    _add_provider(sp_serve)
    sp_serve.add_argument("--host", default=None)
    sp_serve.add_argument("--port", type=int, default=None)
    sp_login = sub.add_parser("login", help="one-time headed login (needs a display)")
    _add_provider(sp_login)
    sub.add_parser("install", help="pre-download the stealth browser binary")
    sp_research = sub.add_parser(
        "research", help="submit a research job to a running `serve` and print the report"
    )
    sp_research.add_argument("query")
    sp_research.add_argument("--host", default="127.0.0.1")
    sp_research.add_argument("--port", type=int, default=5102)

    args = p.parse_args(argv)
    cmd = args.cmd or "serve"
    if cmd == "login":
        return _login(args.provider)
    if cmd == "install":
        return _install()
    if cmd == "research":
        return _research(args.query, args.host, args.port)
    # default: serve
    return _serve(
        getattr(args, "provider", DEFAULT_PROVIDER),
        getattr(args, "host", None),
        getattr(args, "port", None),
    )
