"""copilot `authed()` / `login_steer()`: a signed-in session is detected by being
on the `/chat` app path (or a real composer), never by the page title (the
marketing homepage keeps "... - Sign in" while logged in). login_steer nudges an
off-app post-login landing (office.com) back to the chat app."""

import pytest

from webllm_proxy.providers.copilot import NAV_URL, authed, login_steer


class _Page:
    def __init__(self, url, composer=False):
        self.url = url
        self._composer = composer

    def evaluate(self, _js):  # stands in for the composer-presence probe
        return self._composer


@pytest.mark.parametrize(
    ("url", "composer", "expected"),
    [
        # on the chat app path -> signed in (no DOM probe needed)
        ("https://m365.cloud.microsoft/chat", False, True),
        ("https://m365.cloud.microsoft/chat/", False, True),
        # app host, not /chat: decide by composer presence
        ("https://m365.cloud.microsoft/", True, True),  # app rendered in place
        ("https://m365.cloud.microsoft/", False, False),  # signed-out splash
        ("https://copilot.microsoft.com/", True, True),  # consumer edition
        ("https://copilot.microsoft.com/", False, False),
        # login-host bounce -> signed out (even if a stray textbox exists)
        ("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?x=1", True, False),
        ("https://login.live.com/oauth20_authorize.srf", False, False),
        # off-app post-login drift / unrelated host -> not authed
        ("https://www.office.com/?trysignin=0", False, False),
        ("https://example.com/", True, False),
    ],
)
def test_authed(url, composer, expected):
    assert authed(_Page(url, composer)) is expected


def test_authed_bad_page_is_false():
    class Boom:
        @property
        def url(self):
            raise RuntimeError("detached")

    assert authed(Boom()) is False


class _NavPage:
    def __init__(self, url):
        self.url = url
        self.goto_url = None

    def goto(self, url, **_kw):
        self.goto_url = url


def test_login_steer_reroutes_office_com_landing():
    p = _NavPage("https://www.office.com/?trysignin=0")
    login_steer(p)
    assert p.goto_url == NAV_URL


def test_login_steer_leaves_login_page_alone():
    p = _NavPage("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
    login_steer(p)
    assert p.goto_url is None


def test_login_steer_leaves_chat_app_alone():
    p = _NavPage("https://m365.cloud.microsoft/chat")
    login_steer(p)
    assert p.goto_url is None
