"""Process / lock hygiene for profile-scoped Chrome, cross-platform via psutil.

Clears stale singleton locks (so a prior crash can't block launch) and kills any
Chrome bound to a given profile dir (so shutdown leaves no orphans)."""

import contextlib
import logging
import os
from pathlib import Path

import psutil

log = logging.getLogger(__name__)


def _cloak_binary_roots() -> list[str]:
    """Dirs CloakBrowser's Chromium may live under, normalized for prefix match.
    Mirrors cloakbrowser's own path logic (`CLOAKBROWSER_CACHE_DIR` /
    `~/.cloakbrowser`, plus a `CLOAKBROWSER_BINARY_PATH` override) without
    importing it."""
    roots: list[str] = []
    binpath = os.environ.get("CLOAKBROWSER_BINARY_PATH")
    if binpath:
        roots.append(os.path.normcase(str(Path(binpath).parent)))
    cache = os.environ.get("CLOAKBROWSER_CACHE_DIR")
    base = Path(cache) if cache else Path.home() / ".cloakbrowser"
    roots.append(os.path.normcase(str(base)))
    return roots


def _is_cloak_binary(proc: psutil.Process) -> bool | None:
    """True/False if the process executable is/isn't CloakBrowser's Chromium;
    None when the path can't be read (caller falls back to the cmdline match so
    orphan cleanup is never weakened)."""
    try:
        exe = proc.exe()
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess, OSError):
        return None
    if not exe:
        return None
    exe_n = os.path.normcase(str(Path(exe)))
    return any(exe_n.startswith(root) for root in _cloak_binary_roots())


def clean_singleton_locks(profile: Path) -> None:
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.debug("could not remove %s: %s", name, e)


def _cmdline_str(proc: psutil.Process) -> str:
    try:
        return " ".join(proc.cmdline())
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return ""


def profile_chrome_pids(profile: str) -> list[int]:
    def matches(proc: psutil.Process) -> bool:
        cmd = _cmdline_str(proc)
        if "chrome" not in cmd.lower() or profile not in cmd:
            return False
        # Extra guard so we can never match the user's *real* Chrome even if its
        # command line happened to contain our profile path: require the binary to
        # be CloakBrowser's. If the exe can't be read, `None` falls back to the
        # cmdline match above (keeps orphan cleanup working).
        return _is_cloak_binary(proc) is not False

    return [proc.pid for proc in psutil.process_iter(["pid"]) if matches(proc)]


def _ignore_gone(fn) -> None:
    with contextlib.suppress(psutil.NoSuchProcess, psutil.AccessDenied):
        fn()


def kill_profile_chrome(profile: str) -> int:
    procs = [psutil.Process(pid) for pid in profile_chrome_pids(profile) if psutil.pid_exists(pid)]
    for proc in procs:
        _ignore_gone(proc.terminate)
    if procs:
        _, alive = psutil.wait_procs(procs, timeout=1.0)
        for proc in alive:
            _ignore_gone(proc.kill)
    return len(procs)
