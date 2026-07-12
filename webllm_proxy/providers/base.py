"""Shared base for browser-backed providers.

Every CloakBrowser provider repeats the same config boilerplate: read
`host/port/profile_dir/headless/nav_url` from its env-driven `config` module.
`BrowserProvider` implements that once; concrete providers just set `config` and
implement the browser hooks (`authed`/`trigger`/`capture_match`/
`make_accumulator`) + `register_routes`.

The full browser contract still lives on `domain.ports.Provider` (unchanged);
this base only supplies the concrete config properties, so `Provider` stays the
single ABC the server/CLI depend on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..domain.ports import Provider


class BrowserProvider(Provider):
    #: the provider's env-driven config module; must expose
    #: HOST, PORT, PROFILE_DIR, HEADLESS and NAV_URL.
    config: Any

    def __init__(self, host: str | None = None, port: int | None = None):
        self._host = host or self.config.HOST
        self._port = port or self.config.PORT

    @property
    def profile_dir(self) -> Path:
        return self.config.PROFILE_DIR

    @property
    def nav_url(self) -> str:
        return self.config.NAV_URL

    @property
    def headless(self) -> bool:
        return self.config.HEADLESS

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port
