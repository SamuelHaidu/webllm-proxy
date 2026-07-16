"""Import the user's *installed*-Chrome extensions into a provider's CloakBrowser
profile -- conservatively, and in an antivirus/EDR-conscious way.

This module is deliberately **extension-only**. It only ever globs
``<user_data_dir>/<profile>/Extensions/<id>/<version>/`` and reads each
extension's ``manifest.json``. It never opens, reads, or copies credential
stores (``Login Data``, ``Cookies``, ``Web Data``, ``Local State``,
``Network/``, ``Local Extension Settings/``, ``IndexedDB/``, ...) -- the files an
infostealer would target. A hard denylist (``_denied``) enforces that even if the
globs are ever changed, and profile names are validated so config can't redirect
the read at a credential store.

Two entry points, deliberately split so the long-running server never touches the
real Chrome profile:

- ``import_extensions(pc, provider)`` -- reads the real Chrome profile and copies
  the extensions into our own data dir. Call it ONLY from an explicit,
  user-initiated step (``webllm-proxy login`` / ``webllm-proxy import-extensions``).
- ``imported_extension_paths(pc, provider)`` -- lists what was already copied under
  our data dir. The server uses this; it never accesses the real Chrome dir.

All failures are non-fatal: every public function logs a warning and returns
``[]`` rather than breaking login/serve.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
from pathlib import Path

from .env import data_dir

log = logging.getLogger(__name__)

# Names we must never read or copy: the browser credential stores. Matched
# case-insensitively against a single path component. Scoping every read to
# `Extensions/<id>/<ver>/` already excludes these; this is a belt-and-suspenders
# guard against a future change (or a hostile `chrome_profile` value) reaching a
# credential store.
_DENY_EXACT = frozenset(
    {
        "login data",
        "login data-journal",
        "login data for account",
        "cookies",
        "cookies-journal",
        "web data",
        "web data-journal",
        "local state",
        "network",
        "sessions",
        "session storage",
        "local extension settings",
        "sync extension settings",
        "managed extension settings",
        "extension state",
        "extension rules",
        "indexeddb",
        "databases",
        "history",
        "history-journal",
    }
)


def _denied(name: str) -> bool:
    low = name.strip().lower()
    return low in _DENY_EXACT or "token" in low


def default_chrome_user_data_dir() -> Path | None:
    """Best-effort location of the installed Chrome's "User Data" dir. Windows is
    the primary target; macOS/Linux (incl. Flatpak) are covered too."""
    system = platform.system()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        return Path(local) / "Google" / "Chrome" / "User Data" if local else None
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    # Linux: native install first, then the Flatpak config path.
    candidates = [
        Path.home() / ".config" / "google-chrome",
        Path.home() / ".var" / "app" / "com.google.Chrome" / "config" / "google-chrome",
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return candidates[0]


def _safe_profile(profile: str) -> str:
    """Reject a `chrome_profile` that could escape the `Extensions/` subtree or
    name a credential store (path separators, `..`, or a denied name)."""
    p = (profile or "Default").strip()
    bad_sep = os.sep in p or (os.altsep is not None and os.altsep in p)
    if not p or p == ".." or bad_sep or _denied(p):
        raise ValueError(f"unsafe chrome_profile: {profile!r}")
    return p


def _version_key(name: str) -> list[int]:
    """Sort key for Chrome extension version dirs like ``1.2.3_0``."""
    return [int(part) if part.isdigit() else 0 for part in re.split(r"[._]", name)]


def _is_theme(manifest: Path) -> bool:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and "theme" in data


def _newest_version(id_dir: Path, skip_themes: bool) -> Path | None:
    """Newest version dir under one extension id that has a (non-theme) manifest."""
    versions = []
    for ver_dir in id_dir.iterdir():
        manifest = ver_dir / "manifest.json"
        if not ver_dir.is_dir() or not manifest.is_file():
            continue
        if skip_themes and _is_theme(manifest):
            continue
        versions.append(ver_dir)
    return max(versions, key=lambda p: _version_key(p.name)) if versions else None


def discover_extensions(
    user_data_dir: Path | str, profile: str = "Default", skip_themes: bool = True
) -> list[Path]:
    """Newest valid version dir of every extension installed in `profile`.

    Only globs `<user_data_dir>/<profile>/Extensions/*/*/` and reads
    `manifest.json`; never walks the rest of the profile."""
    ext_root = Path(user_data_dir) / _safe_profile(profile) / "Extensions"
    if not ext_root.is_dir():
        return []
    best: dict[str, Path] = {}
    for id_dir in ext_root.iterdir():
        if not id_dir.is_dir() or _denied(id_dir.name):
            continue
        newest = _newest_version(id_dir, skip_themes)
        if newest is not None:
            best[id_dir.name] = newest
    return [best[k] for k in sorted(best)]


def copy_extensions(ext_dirs: list[Path], dest_root: Path | str) -> list[str]:
    """Copy each extension's version dir to `dest_root/<id>/<version>/`.

    Idempotent (same version already copied -> skipped) and self-contained: older
    versions of the same extension are pruned so exactly one loads."""
    dest_root = Path(dest_root)
    dest_root.mkdir(parents=True, exist_ok=True)
    out: list[str] = []
    for raw in ext_dirs:
        src = Path(raw)
        # Guard: only ever copy something that actually lives under `Extensions/`.
        if "Extensions" not in src.parts:
            log.warning("chrome_import: refusing to copy non-extension path %s", src)
            continue
        ext_id, version = src.parent.name, src.name
        id_dir = dest_root / ext_id
        dest = id_dir / version
        if dest.is_dir() and (dest / "manifest.json").is_file():
            out.append(str(dest.resolve()))
            continue
        id_dir.mkdir(parents=True, exist_ok=True)
        tmp = id_dir / f"{version}.partial"
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            shutil.copytree(src, tmp)
            tmp.replace(dest)
        except OSError:
            shutil.rmtree(tmp, ignore_errors=True)
            log.warning("chrome_import: failed to copy %s@%s", ext_id, version, exc_info=True)
            continue
        for other in id_dir.iterdir():  # prune stale versions of this extension
            if other.is_dir() and other.name != version:
                shutil.rmtree(other, ignore_errors=True)
        out.append(str(dest.resolve()))
    return out


def _dest_root(provider_name: str) -> Path:
    return data_dir(f"{provider_name}-proxy") / "imported_extensions"


def import_extensions(pc, provider_name: str) -> list[str]:
    """Read the real Chrome profile and copy its extensions into our data dir.

    The ONLY function here that touches the installed Chrome. Call from an
    explicit, user-initiated step -- never from the server."""
    if not getattr(pc, "import_chrome_extensions", False):
        return []
    try:
        raw = getattr(pc, "chrome_user_data_dir", None)
        udd = Path(raw) if raw else default_chrome_user_data_dir()
        if udd is None or not Path(udd).is_dir():
            log.warning(
                "[%s] import_chrome_extensions: Chrome 'User Data' dir not found (%s)",
                provider_name,
                udd,
            )
            return []
        srcs = discover_extensions(udd, getattr(pc, "chrome_profile", "Default"))
        if not srcs:
            log.info(
                "[%s] import_chrome_extensions: no extensions found under %s",
                provider_name,
                Path(udd) / getattr(pc, "chrome_profile", "Default") / "Extensions",
            )
            return []
        paths = copy_extensions(srcs, _dest_root(provider_name))
        log.info("[%s] imported %d Chrome extension(s)", provider_name, len(paths))
        return paths
    except Exception:
        log.warning(
            "[%s] import_chrome_extensions failed; continuing without", provider_name, exc_info=True
        )
        return []


def imported_extension_paths(pc, provider_name: str) -> list[str]:
    """Absolute dirs of already-imported extensions (newest version each). Used by
    the server; never reads the real Chrome profile."""
    if not getattr(pc, "import_chrome_extensions", False):
        return []
    try:
        root = _dest_root(provider_name)
        if not root.is_dir():
            return []
        out: list[str] = []
        for id_dir in sorted(root.iterdir()):
            if not id_dir.is_dir():
                continue
            versions = [
                v for v in id_dir.iterdir() if v.is_dir() and (v / "manifest.json").is_file()
            ]
            if versions:
                out.append(str(max(versions, key=lambda p: _version_key(p.name)).resolve()))
        return out
    except Exception:
        log.warning("[%s] listing imported extensions failed", provider_name, exc_info=True)
        return []
