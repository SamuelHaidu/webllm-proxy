"""Copilot provider: browser-backed Microsoft Copilot (M365 BizChat / consumer)
exposed as an OpenAI-compatible surface.

Unlike the raw-`websockets` SDK in this same package, the *provider* runs turns
through the shared CloakBrowser session: it types the message into the page, the
page opens its ChatHub WebSocket, and `transport/browser.py` captures the
server->client frames over CDP. Running inside the real logged-in page reuses the
existing browser infra (so `login`/`serve` work like the other providers) and
avoids the request-level abuse-clamp that raw off-browser replay triggers.

Note (v1): model/tone selection is accepted on the request but not yet forced in
the page UI; the turn uses whatever model the composer has selected. Wiring the
model selector (and richer conversation continuity) is the next tuning pass.
"""

from __future__ import annotations

import logging

from ...domain.ports import Accumulator
from ..base import BrowserProvider
from . import config
from .accumulator import CopilotAccumulator
from .editions import get_edition

log = logging.getLogger(__name__)


class CopilotProvider(BrowserProvider):
    name = "copilot"
    config = config

    def __init__(self, host: str | None = None, port: int | None = None):
        super().__init__(host, port)
        self._edition = get_edition(config.EDITION)

    # ---- browser hooks ---------------------------------------------------
    def authed(self, page) -> bool:
        try:
            url = (page.url or "").lower()
        except Exception:
            return False
        if "login.microsoftonline.com" in url or "login.live.com" in url:
            return False
        return "m365.cloud.microsoft" in url or "copilot.microsoft.com" in url

    def capture_match(self, url: str) -> bool:
        return any(sub in url for sub in config.CHATHUB_MATCH)

    def trigger(self, page, job) -> None:
        turn = job.payload  # domain.conversation.ChatTurn
        if getattr(turn, "new_conversation", False):
            page.goto(config.NAV_URL, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(1500)
        composer = self._find_composer(page)
        if composer is None:
            raise RuntimeError("copilot composer not found")
        composer.click()
        page.wait_for_timeout(150)
        page.keyboard.insert_text(turn.message)
        page.wait_for_timeout(150)
        page.keyboard.press("Enter")

    @staticmethod
    def _find_composer(page):
        for sel in (
            '[role="textbox"]',
            'div[contenteditable="true"]',
            "textarea",
            "#m365-chat-editor-target-element",
        ):
            loc = page.locator(sel).first
            try:
                if loc.count() and loc.is_visible():
                    return loc
            except Exception:
                pass
        return None

    def make_accumulator(self) -> Accumulator:
        return CopilotAccumulator(self._edition.make_codec())

    # ---- runtime helper used by routes -----------------------------------
    def list_models(self):
        """The models this edition offers (default set; live discovery needs the
        shell session)."""
        return self._edition.default_models()

    # ---- HTTP surface ----------------------------------------------------
    def register_routes(self, app, session) -> None:
        from .routes import register_copilot

        register_copilot(app, session, self)

    def banner(self, host, port):
        return [
            f"  GET  http://{host}:{port}/v1/models",
            f"  POST http://{host}:{port}/v1/chat/completions   (OpenAI; Microsoft Copilot)",
        ]
