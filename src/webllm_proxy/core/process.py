"""Process / lock hygiene for profile-scoped Chrome.

Clears stale singleton locks (so a prior crash can't block launch) and kills any
Chrome bound to a given profile dir (so shutdown leaves no orphans). Shared by
every provider — the browser layer is the same regardless of backend.
"""
import logging
import os
import signal
import time
from pathlib import Path

log = logging.getLogger(__name__)


def clean_singleton_locks(profile: Path) -> None:
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
        except FileNotFoundError:
            pass
        except OSError as e:
            log.debug("could not remove %s: %s", name, e)


def profile_chrome_pids(profile: str) -> list[int]:
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.exists():
        return pids
    for d in proc.iterdir():
        if not d.name.isdigit():
            continue
        try:
            cmd = (d / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "chrome" in cmd and profile in cmd:
            pids.append(int(d.name))
    return pids


def kill_profile_chrome(profile: str) -> int:
    pids = profile_chrome_pids(profile)
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if pids:
        time.sleep(1.0)
        for pid in profile_chrome_pids(profile):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    return len(pids)
