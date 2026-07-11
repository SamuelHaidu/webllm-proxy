"""Small environment + cross-platform data-directory helpers shared by every
provider's config."""

import os
from pathlib import Path

from platformdirs import user_data_dir


def data_dir(app: str) -> Path:
    """Per-OS data dir for `app` (e.g. a provider's login profile lives here,
    out of the repo, so it survives and stays private). On Linux/macOS this
    resolves exactly as before (platformdirs honors XDG_DATA_HOME the same way),
    so existing `~/.local/share/<app>` profiles keep working; on Windows it now
    resolves to `%LOCALAPPDATA%\\<app>` instead of falling back to a POSIX-only
    path that doesn't exist there."""
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
