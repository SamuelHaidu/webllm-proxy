"""CLI entry point.

  chatgpt-proxy [serve]   run the API server (default)
  chatgpt-proxy login     one-time headed ChatGPT login (needs a display)
  chatgpt-proxy install   pre-download the stealth browser binary
"""
import argparse
import logging
import signal
import sys

from . import config
from ._version import __version__

log = logging.getLogger(__name__)


def _serve() -> int:
    from .browser import BrowserSession
    from .server import create_app

    session = BrowserSession()
    session.start()
    print(f"Booting browser (profile: {config.PROFILE_DIR}, headless={config.HEADLESS}) ...")
    if not session.wait_ready(90):
        print("FATAL:", session.error or "browser did not become ready in time", file=sys.stderr)
        session.close()
        return 1

    app = create_app(session)

    def _shutdown(_signum, _frame):
        print("\nShutting down ...")
        session.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)

    print(f"Ready. OpenAI-compatible proxy on http://{config.HOST}:{config.PORT}")
    print(f"  GET  http://{config.HOST}:{config.PORT}/v1/models")
    print(f"  POST http://{config.HOST}:{config.PORT}/v1/chat/completions")
    try:
        app.run(host=config.HOST, port=config.PORT, threaded=True,
                debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        session.close()
    return 0


def _login() -> int:
    from .browser import run_login
    return 0 if run_login() else 1


def _install() -> int:
    from cloakbrowser import ensure_binary
    print("Downloading the stealth browser binary (first run only) ...")
    print("Ready:", ensure_binary())
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(
        prog="chatgpt-proxy",
        description="OpenAI-compatible local proxy over the ChatGPT web app.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the API server (default)")
    sub.add_parser("login", help="one-time headed ChatGPT login (needs a display)")
    sub.add_parser("install", help="pre-download the stealth browser binary")
    args = p.parse_args(argv)

    cmd = args.cmd or "serve"
    if cmd == "login":
        return _login()
    if cmd == "install":
        return _install()
    return _serve()


if __name__ == "__main__":
    sys.exit(main())
