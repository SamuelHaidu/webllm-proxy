"""Process / lock hygiene for profile-scoped Chrome, cross-platform via psutil.

Clears stale singleton locks (so a prior crash can't block launch) and kills any
Chrome bound to a given profile dir (so shutdown leaves no orphans)."""

import contextlib
import logging
from pathlib import Path

import psutil

log = logging.getLogger(__name__)


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
        return "chrome" in cmd.lower() and profile in cmd

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
