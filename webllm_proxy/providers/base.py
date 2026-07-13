"""The one seam left in the app: a provider exposes exactly two methods,
`models()` and `completions()`. Everything else (browser transport, wire
conversion, tags) is a plain utility the provider calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from ..gateways.cloakbrowser import BrowserSession


@runtime_checkable
class Provider(Protocol):
    name: str

    def models(self) -> list[dict]:
        """OpenAI `/v1/models` `data` entries; ids namespaced `<name>__<slug>`."""
        ...

    def completions(self, request: dict) -> dict | Iterator[bytes]:
        """An OpenAI `chat.completion` (non-stream) or an SSE byte iterator
        (stream). Handles tools + reasoning internally."""
        ...


class BrowserBackedProvider:
    """Shared lifecycle for the three browser-backed providers: owns a
    `BrowserSession` and forwards readiness. Subclasses implement `models()` /
    `completions()`."""

    name = "base"

    def __init__(self, session: BrowserSession):
        self.session = session

    @property
    def ready(self) -> bool:
        return self.session.ready

    @property
    def error(self) -> str | None:
        return self.session.error

    def start(self) -> None:
        self.session.start()

    def wait_ready(self, timeout: float = 90.0) -> bool:
        return self.session.wait_ready(timeout)

    def close(self) -> None:
        self.session.close()
