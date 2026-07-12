"""Credentials.

A `Credential` contributes to the WebSocket URL query and/or the connection
headers. The token may be a plain string or a zero-arg callable (a "provider")
that returns a fresh token — the callable form lets a browser session hand over
a freshly minted, short-lived token (e.g. the M365 8 h Sydney Bearer) on each
connect without this package knowing how it was obtained.
"""

from __future__ import annotations

import abc
from collections.abc import Callable


class Credential(abc.ABC):
    @abc.abstractmethod
    def apply_query(self, params: dict[str, str]) -> None: ...

    def headers(self) -> dict[str, str]:
        return {}


def _resolve(token: str | Callable[[], str]) -> str:
    return token if isinstance(token, str) else token()


class QueryToken(Credential):
    """Bearer/access token passed as a URL query parameter.

    - M365 BizChat / Sydney ChatHub: `param="access_token"`.
    - Consumer `/c/api/chat`: `param="accessToken"`.
    """

    def __init__(self, token: str | Callable[[], str], *, param: str = "access_token") -> None:
        self._token = token
        self.param = param

    def apply_query(self, params: dict[str, str]) -> None:
        params[self.param] = _resolve(self._token)


class HeaderToken(Credential):
    """Bearer token passed as an `Authorization` header instead of the query."""

    def __init__(self, token: str | Callable[[], str]) -> None:
        self._token = token

    def apply_query(self, params: dict[str, str]) -> None:
        return None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {_resolve(self._token)}"}


class Anonymous(Credential):
    """No credential (free consumer session where the token rides elsewhere)."""

    def apply_query(self, params: dict[str, str]) -> None:
        return None
