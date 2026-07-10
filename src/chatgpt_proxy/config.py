"""Configuration, resolved from environment with sensible defaults.

The browser profile (which holds the ChatGPT login) lives under the user's
XDG data dir by default, not in the repo/CWD, so it survives and stays private.
"""
import os
from pathlib import Path

CHATGPT_URL = "https://chatgpt.com"


def _data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return Path(base) / "chatgpt-proxy"


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


# Where the persistent CloakBrowser profile (the login) lives.
PROFILE_DIR = Path(os.environ.get("CHATGPT_PROXY_PROFILE") or (_data_dir() / "profile"))

# Run the browser headless (the default). A display is only needed for `login`.
HEADLESS = _flag("CHATGPT_PROXY_HEADLESS", True)

# HTTP server bind.
HOST = os.environ.get("CHATGPT_PROXY_HOST", "127.0.0.1")
PORT = int(os.environ.get("CHATGPT_PROXY_PORT", "5102"))

# Dump each request's incoming payload + what we forward, to /tmp, for debugging.
DEBUG_DUMP = _flag("CHATGPT_PROXY_DEBUG_DUMP", False)
