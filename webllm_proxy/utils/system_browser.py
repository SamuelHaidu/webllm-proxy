"""Drive the user's *installed* Edge/Chrome directly (Playwright's browser
`channel`) on their real profile, instead of the bundled stealth Chromium.

This is the "just use my real browser" path: because it's literally the user's
Edge/Chrome opening its own profile, every extension and login already works --
no copying, no fresh-profile state loss. The trade-off is no anti-detect stealth
(fine for databricks/copilot, not for chatgpt's Turnstile/PoW) and the browser
must be fully closed first (a profile can only be open in one instance).

`launch_system_context` returns the Playwright handle + context; the caller owns
closing the context and stopping Playwright.
"""

from __future__ import annotations

import contextlib
import logging
import os
import platform
from pathlib import Path

from .chrome_import import default_chrome_user_data_dir

log = logging.getLogger(__name__)

# Config value -> Playwright channel name.
_CHANNELS = {"edge": "msedge", "chrome": "chrome"}

SYSTEM_BROWSERS = ("edge", "chrome")


def channel_for(browser: str) -> str:
    try:
        return _CHANNELS[browser]
    except KeyError:
        raise ValueError(
            f"unknown system browser {browser!r} (choose from: edge, chrome)"
        ) from None


def _edge_user_data_dir() -> Path | None:
    system = platform.system()
    if system == "Windows":
        local = os.environ.get("LOCALAPPDATA")
        return Path(local) / "Microsoft" / "Edge" / "User Data" if local else None
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Microsoft Edge"
    for c in (
        Path.home() / ".config" / "microsoft-edge",
        Path.home() / ".config" / "microsoft-edge-beta",
    ):
        if c.is_dir():
            return c
    return Path.home() / ".config" / "microsoft-edge"


def resolve_user_data_dir(browser: str, override: str | None = None) -> Path | None:
    """The installed browser's "User Data" root (Windows Edge:
    `%LOCALAPPDATA%\\Microsoft\\Edge\\User Data`). `override` wins."""
    if override:
        return Path(override)
    if browser == "edge":
        return _edge_user_data_dir()
    if browser == "chrome":
        return default_chrome_user_data_dir()
    raise ValueError(f"unknown system browser {browser!r}")


def launch_system_context(
    *,
    browser: str,
    profile: str = "Default",
    user_data_dir: str | None = None,
    headless: bool = True,
):
    """Start Playwright and launch the installed `browser` on its real profile.

    Returns `(playwright, context, resolved_user_data_dir)`. The caller must
    `context.close()` and `playwright.stop()` on shutdown. Raises with an
    actionable message if the browser can't be launched (usually: still open)."""
    from playwright.sync_api import sync_playwright

    udd = resolve_user_data_dir(browser, user_data_dir)
    if udd is None or not Path(udd).is_dir():
        raise RuntimeError(
            f"{browser} 'User Data' dir not found ({udd}); set browser_user_data_dir explicitly."
        )
    channel = channel_for(browser)
    log.info(
        "[system-browser] launching %s (channel=%s, headless=%s) profile=%s dir=%s",
        browser,
        channel,
        headless,
        profile,
        udd,
    )
    pw = sync_playwright().start()
    try:
        ctx = pw.chromium.launch_persistent_context(
            str(udd),
            channel=channel,
            headless=headless,
            no_viewport=True,
            args=[f"--profile-directory={profile}"],
        )
    except Exception as e:
        with contextlib.suppress(Exception):
            pw.stop()
        raise RuntimeError(
            f"could not launch {browser} on profile {profile!r} at {udd}. "
            f"Make sure {browser} is fully closed first (a profile can only be open "
            f"in one instance). Original error: {e}"
        ) from e
    return pw, ctx, udd
