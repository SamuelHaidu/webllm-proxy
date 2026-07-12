"""WebSocket transport abstraction.

The default implementation uses the `websockets` library (lazy-imported so the
package imports without it). Swap in your own `Transport` — e.g. one that drives
CloakBrowser/CDP and captures frames in-page — to bypass off-browser
replay-binding or anti-bot. That is exactly the seam `webllm_proxy` would use.
"""

from __future__ import annotations

import abc

from .exceptions import TransportError


class Transport(abc.ABC):
    @abc.abstractmethod
    async def connect(self, url: str, headers: dict | None = None) -> None: ...

    @abc.abstractmethod
    async def send(self, data: str) -> None: ...

    @abc.abstractmethod
    async def recv(self) -> str: ...

    @abc.abstractmethod
    async def close(self) -> None: ...


class WebsocketsTransport(Transport):
    """`websockets`-backed transport. Requires `pip install websockets`."""

    def __init__(self) -> None:
        self._ws = None

    async def connect(self, url: str, headers: dict | None = None) -> None:
        try:
            import websockets
        except ImportError as e:  # pragma: no cover
            raise TransportError("the `websockets` package is required") from e
        # `additional_headers` (websockets >= 12) vs `extra_headers` (older).
        try:
            self._ws = await websockets.connect(url, additional_headers=headers, max_size=None)
        except TypeError:
            self._ws = await websockets.connect(url, extra_headers=headers, max_size=None)

    async def send(self, data: str) -> None:
        if self._ws is None:
            raise TransportError("transport not connected")
        await self._ws.send(data)

    async def recv(self) -> str:
        if self._ws is None:
            raise TransportError("transport not connected")
        data = await self._ws.recv()
        return data if isinstance(data, str) else data.decode("utf-8", "replace")

    async def close(self) -> None:
        if self._ws is not None:
            try:
                await self._ws.close()
            finally:
                self._ws = None
