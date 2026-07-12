"""Bridge from captured ChatHub WebSocket frames to the proxy's `Event` stream.

Each server->client WS frame (one call to `feed`) is decoded by the moved
protocol codec (`SignalRCodec` for m365, `EventCodec` for consumer) into
normalized items; we translate answer `Delta`s to `("content", text)` and the
terminal `Final` to `("done", ...)`. Challenge/keepalive control items are
ignored — the page owns the socket and answers those itself; we only observe.
"""

from __future__ import annotations

from collections.abc import Iterable

from ...domain.ports import Accumulator, Event
from .models import Delta, Final, Progress
from .protocol.base import ProtocolCodec


class CopilotAccumulator(Accumulator):
    def __init__(self, codec: ProtocolCodec):
        self._codec = codec

    def feed(self, chunk: str) -> Iterable[Event]:
        events: list[Event] = []
        for item in self._codec.decode(chunk):
            if isinstance(item, Delta):
                if item.text:
                    events.append(("content", item.text))
            elif isinstance(item, Progress):
                # search / thinking / tool progress — not part of the answer text
                continue
            elif isinstance(item, Final):
                # Deltas already carried the text; just close the turn.
                self.finish_reason = "stop"
                events.append(("done", "stop"))
        return events
