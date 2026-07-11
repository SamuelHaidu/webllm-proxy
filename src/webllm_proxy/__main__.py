"""Unified CLI.

  webllm-proxy serve   --provider chatgpt|databricks   run the API server (default)
  webllm-proxy login   --provider chatgpt|databricks   one-time headed login (needs a display)
  webllm-proxy install                                 pre-download the stealth browser binary

The provider may also be set with WEBLLM_PROXY_PROVIDER (default: chatgpt).
"""
import argparse
import logging
import os
import signal
import sys

from ._version import __version__
from .providers import PROVIDERS, get_provider

log = logging.getLogger(__name__)

DEFAULT_PROVIDER = os.environ.get("WEBLLM_PROXY_PROVIDER", "chatgpt")


def _serve(name: str, host: str | None, port: int | None) -> int:
    from .core.browser import BrowserSession
    from .server import create_app

    provider = get_provider(name, host, port)
    try:
        _ = provider.nav_url  # surface missing config (e.g. workspace URL) early
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    session = BrowserSession(provider)
    session.start()
    print(f"[{provider.name}] booting browser "
          f"(profile={provider.profile_dir}, headless={provider.headless}) ...")
    if not session.wait_ready(90):
        print("FATAL:", session.error or "browser did not become ready in time", file=sys.stderr)
        session.close()
        return 1

    app = create_app(session, provider)

    def _shutdown(_signum, _frame):
        print("\nShutting down ...")
        session.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    print(f"[{provider.name}] ready on http://{provider.host}:{provider.port}")
    for line in provider.banner(provider.host, provider.port):
        print(line)
    try:
        app.run(host=provider.host, port=provider.port, threaded=True,
                debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    return 0


def _login(name: str) -> int:
    from .core.browser import run_login
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
    print("Ready:", ensure_binary())
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(
        prog="webllm-proxy",
        description="Browser-backed local API bridges over login-only web LLMs "
                    "(ChatGPT web, Databricks Genie/llmproxy).")
    p.add_argument("--version", action="version", version=__version__)

    def _add_provider(sp):
        sp.add_argument("-p", "--provider", choices=PROVIDERS, default=DEFAULT_PROVIDER,
                        help="which backend (default: %(default)s)")

    sub = p.add_subparsers(dest="cmd")
    sp_serve = sub.add_parser("serve", help="run the API server (default)")
    _add_provider(sp_serve)
    sp_serve.add_argument("--host", default=None)
    sp_serve.add_argument("--port", type=int, default=None)
    sp_login = sub.add_parser("login", help="one-time headed login (needs a display)")
    _add_provider(sp_login)
    sub.add_parser("install", help="pre-download the stealth browser binary")

    args = p.parse_args(argv)
    cmd = args.cmd or "serve"
    if cmd == "login":
        return _login(args.provider)
    if cmd == "install":
        return _install()
    # default: serve
    return _serve(getattr(args, "provider", DEFAULT_PROVIDER),
                  getattr(args, "host", None), getattr(args, "port", None))


if __name__ == "__main__":
    sys.exit(main())
