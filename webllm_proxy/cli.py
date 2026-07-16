"""Argparse CLI, imported by the thin `__main__.py` entry point.

webllm-proxy serve  --config-file ./webllm-proxy.yaml   run all enabled providers on one server
webllm-proxy login  --provider chatgpt|databricks|copilot   one-time headed login (needs a display)
webllm-proxy install                                    pre-download the stealth browser binary
"""

import argparse
import contextlib
import signal
import sys

from ._version import __version__
from .utils.logging import configure_logging

DEFAULT_CONFIG = "./webllm-proxy.yaml"

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

PROVIDERS = ("chatgpt", "databricks", "copilot")


def _register_shutdown(handler) -> None:
    for sig_name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        with contextlib.suppress(ValueError, OSError):
            signal.signal(sig, handler)


def _serve(config_file: str) -> int:
    from .server import serve
    from .utils.config import load_config

    try:
        config = load_config(config_file)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    def _shutdown(_signum, _frame):
        print("\nShutting down ...")
        sys.exit(0)

    _register_shutdown(_shutdown)
    return serve(config)


def _login(name: str, config_file: str) -> int:
    from .providers import login
    from .utils.config import Config, load_config

    try:
        config = load_config(config_file)
    except FileNotFoundError:
        config = Config()  # login works off defaults; databricks still needs a URL
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2
    try:
        return 0 if login(name, config) else 1
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2


def _import_extensions(name: str, config_file: str) -> int:
    from .utils.chrome_import import import_extensions
    from .utils.config import Config, load_config

    try:
        config = load_config(config_file)
    except FileNotFoundError:
        config = Config()
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    pc = getattr(config.providers, name)
    if not pc.import_chrome_extensions:
        print(
            f"[{name}] import_chrome_extensions is not enabled; "
            f"set providers.{name}.import_chrome_extensions: true in {config_file} first."
        )
        return 0
    paths = import_extensions(pc, name)
    if not paths:
        print(f"[{name}] no extensions imported (no installed-Chrome profile/extensions found).")
        return 0
    print(f"[{name}] imported {len(paths)} extension(s) into the proxy profile:")
    for p in paths:
        print(f"  {p}")
    return 0


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


def main(argv=None) -> int:
    configure_logging()
    p = argparse.ArgumentParser(
        prog="webllm-proxy",
        description="Browser-backed local OpenAI-compatible bridge over login-only web LLMs "
        "(ChatGPT web, Databricks Genie/llmproxy, Microsoft Copilot).",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd")

    sp_serve = sub.add_parser("serve", help="run all enabled providers on one server")
    sp_serve.add_argument("--config-file", default=DEFAULT_CONFIG)

    sp_login = sub.add_parser("login", help="one-time headed login (needs a display)")
    sp_login.add_argument("--provider", choices=PROVIDERS, required=True)
    sp_login.add_argument("--config-file", default=DEFAULT_CONFIG)

    sp_imp = sub.add_parser(
        "import-extensions",
        help="copy the installed Chrome's extensions into a provider profile "
        "(needs import_chrome_extensions: true)",
    )
    sp_imp.add_argument("--provider", choices=PROVIDERS, required=True)
    sp_imp.add_argument("--config-file", default=DEFAULT_CONFIG)

    sub.add_parser("install", help="pre-download the stealth browser binary")

    args = p.parse_args(argv)
    cmd = args.cmd or "serve"
    if cmd == "login":
        return _login(args.provider, args.config_file)
    if cmd == "import-extensions":
        return _import_extensions(args.provider, args.config_file)
    if cmd == "install":
        return _install()
    return _serve(getattr(args, "config_file", DEFAULT_CONFIG))
