"""utils.system_browser + session routing: drive the installed Edge/Chrome on its
real profile. No real browser is launched -- path resolution, channel mapping, the
missing-dir error, and the session's launch routing are all exercised in-process."""

from pathlib import Path

import pytest

from webllm_proxy.gateways.cloakbrowser import session as session_mod
from webllm_proxy.gateways.cloakbrowser.session import BrowserSession
from webllm_proxy.utils import system_browser as sb


def test_channel_for():
    assert sb.channel_for("edge") == "msedge"
    assert sb.channel_for("chrome") == "chrome"
    with pytest.raises(ValueError):
        sb.channel_for("firefox")


def test_resolve_edge_windows(monkeypatch):
    monkeypatch.setattr(sb.platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\me\AppData\Local")
    assert sb.resolve_user_data_dir("edge") == (
        Path(r"C:\Users\me\AppData\Local") / "Microsoft" / "Edge" / "User Data"
    )


def test_resolve_edge_macos(monkeypatch):
    monkeypatch.setattr(sb.platform, "system", lambda: "Darwin")
    assert sb.resolve_user_data_dir("edge") == (
        Path.home() / "Library" / "Application Support" / "Microsoft Edge"
    )


def test_resolve_override_wins():
    assert sb.resolve_user_data_dir("edge", "/custom/x") == Path("/custom/x")


def test_resolve_unknown_browser():
    with pytest.raises(ValueError):
        sb.resolve_user_data_dir("safari")


def test_launch_missing_user_data_dir_is_actionable_error(tmp_path):
    # A missing dir raises before any browser launch, with a "close it first" hint.
    with pytest.raises(RuntimeError) as ei:
        sb.launch_system_context(browser="edge", user_data_dir=str(tmp_path / "nope"))
    assert "User Data" in str(ei.value)


def test_session_routes_to_system_browser(monkeypatch):
    """BrowserSession(browser='edge') launches via system_browser, not CloakBrowser,
    and captures the Playwright handle for shutdown."""
    fake_ctx, fake_pw = object(), object()
    seen = {}

    def fake_launch(*, browser, profile, user_data_dir, headless):
        seen.update(browser=browser, profile=profile, udd=user_data_dir, headless=headless)
        return fake_pw, fake_ctx, "/resolved/dir"

    # session.py imports the symbol at module load, so patch it there.
    monkeypatch.setattr(session_mod, "launch_system_context", fake_launch)
    s = BrowserSession(
        name="databricks",
        nav_url="http://x",
        profile_dir=Path("/tmp/p"),
        headless=True,
        authed=lambda p: True,
        browser="edge",
        browser_profile="Profile 1",
        browser_user_data_dir="/edge",
    )
    ctx = s._launch_context()
    assert ctx is fake_ctx
    assert s._pw is fake_pw
    assert seen == {"browser": "edge", "profile": "Profile 1", "udd": "/edge", "headless": True}
