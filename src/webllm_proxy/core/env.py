"""Small environment helpers shared by every provider's config."""
import os
from pathlib import Path


def data_dir(app: str) -> Path:
    """XDG data dir for `app` (e.g. the browser profile lives here, out of the
    repo, so it survives and stays private)."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share")
    return Path(base) / app


def flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() not in ("0", "false", "no", "off", "")


def env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
