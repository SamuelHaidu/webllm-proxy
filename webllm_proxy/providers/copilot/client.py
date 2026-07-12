"""The universal client. Edition + protocol are pluggable; this orchestration is
identical for every Copilot variant:

    open socket -> open_frames -> encode_send -> pump(decode) -> Final

Anti-bot challenges and keepalives are handled transparently in the pump loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from .auth import Credential
from .editions import Edition, get_edition
from .exceptions import TransportError
from .models import ConversationRef, Delta, Event, Final, Model, ModelInfo, Progress
from .protocol.base import Completed, NeedChallenge
from .transport import Transport, WebsocketsTransport


def _new_http():
    try:
        import httpx
    except ImportError as e:  # pragma: no cover
        raise TransportError("`httpx` is required for create_conversation") from e
    return httpx.AsyncClient(timeout=30)


class CopilotClient:
    def __init__(
        self,
        edition: str | Edition,
        credential: Credential,
        *,
        transport_factory=WebsocketsTransport,
        http=None,
    ) -> None:
        self.edition = get_edition(edition) if isinstance(edition, str) else edition
        self.credential = credential
        self._transport_factory = transport_factory
        self._http = http  # optional shared httpx.AsyncClient

    async def create_conversation(self) -> ConversationRef:
        """Convenience conversation-create (consumer only). M365 raises
        `NotImplementedError` — pass a browser-obtained id to `ask` instead."""
        http = self._http or _new_http()
        try:
            return await self.edition.create_conversation(http, self.credential)
        finally:
            if self._http is None:
                await http.aclose()

    async def list_models(self) -> list[ModelInfo]:
        """Discover the models the service currently offers. Never raises for a
        missing dependency or offline: falls back to `edition.default_models()`.
        Read-only — makes no chat turns."""
        http = self._http
        own = False
        if http is None:
            try:
                http = _new_http()
                own = True
            except TransportError:
                http = None  # no httpx -> discovery falls back to defaults
        try:
            return await self.edition.discover_models(http, self.credential)
        finally:
            if own and http is not None:
                await http.aclose()

    async def ask(
        self,
        text: str,
        *,
        conversation: ConversationRef | str,
        model: Model | str = Model.AUTO,
    ) -> AsyncIterator[Event]:
        """Stream one turn. Yields `Delta`/`Progress`, terminated by one `Final`.
        `model` is a normalized `Model` or a raw discovered id (`ModelInfo.id`)."""
        conv = ConversationRef(id=conversation) if isinstance(conversation, str) else conversation
        codec = self.edition.make_codec()
        url = self.edition.ws_url(conv, self.credential)
        headers = self.edition.ws_headers()
        transport: Transport = self._transport_factory()
        await transport.connect(url, headers)
        try:
            for frame in codec.open_frames():
                await transport.send(frame)
            options = self.edition.send_options(model, conv)
            for frame in codec.encode_send(text, conversation_id=conv.id, options=options):
                await transport.send(frame)
            async for event in self._pump(transport, codec):
                yield event
        finally:
            await transport.close()

    async def ask_text(
        self, text: str, *, conversation: ConversationRef | str, model: Model | str = Model.AUTO
    ) -> Final:
        """Non-streaming convenience: consume the stream, return the `Final`."""
        final: Final | None = None
        async for event in self.ask(text, conversation=conversation, model=model):
            if isinstance(event, Final):
                final = event
        if final is None:
            raise TransportError("stream ended without a final message")
        return final

    async def _pump(self, transport: Transport, codec) -> AsyncIterator[Event]:
        while True:
            try:
                raw = await transport.recv()
            except Exception as e:  # includes websockets ConnectionClosed
                raise TransportError(f"connection closed before completion: {e}") from e
            for item in codec.decode(raw):
                if isinstance(item, Final):
                    yield item
                    return
                if isinstance(item, (Delta, Progress)):
                    yield item
                elif isinstance(item, NeedChallenge):
                    reply = codec.encode_challenge_response(item)
                    if reply is not None:
                        await transport.send(reply)
                elif isinstance(item, Completed):
                    return
                # Pong / Ack: ignore
