"""Small environment + cross-platform data-directory helpers."""

import os
from pathlib import Path

from platformdirs import user_data_dir


def data_dir(app: str) -> Path:
    """Per-OS data dir for `app` (login profiles live here, out of the repo)."""
    return Path(user_data_dir(app, appauthor=False))


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
