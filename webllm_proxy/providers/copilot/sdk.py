"""OpenAI-SDK-style facade over the Copilot engine.

Mirrors the shape people already know from `openai`:

    from webllm_proxy.providers.copilot import Copilot
    client = Copilot(edition="m365", api_key=TOKEN, conversation=CONV_ID)
    resp = client.chat.completions.create(model="think", messages=[{"role":"user","content":"hi"}])
    print(resp.choices[0].message.content)

    for chunk in client.chat.completions.create(model="fast", messages=[...], stream=True):
        print(chunk.choices[0].delta.content or "", end="")

    for m in client.models.list():
        print(m.id, m.title)

`AsyncCopilot` is the async twin (mirrors `AsyncOpenAI`). Both wrap the
`CopilotClient` engine + protocol codecs unchanged; only the surface is new.

Divergences from OpenAI (documented, not bugs): only the last `user` message is
sent per turn (Copilot keeps history server-side via `conversation`); `system`
messages are prepended as context; there is no token `usage` (Copilot doesn't
report counts) — `throttling` is exposed instead.
"""

from __future__ import annotations

import asyncio
import queue
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from .auth import Anonymous, Credential, QueryToken
from .client import CopilotClient
from .exceptions import (
    AuthError,
    CopilotError,
    ThrottledError,
    TransportError,
)
from .models import ConversationRef, Delta, Final, Model, ModelInfo
from .transport import WebsocketsTransport

# ---- OpenAI-style error aliases (same underlying classes) ------------------
APIError = CopilotError
APIConnectionError = TransportError
RateLimitError = ThrottledError
AuthenticationError = AuthError
BadRequestError = CopilotError


# ---- response objects (OpenAI-shaped) --------------------------------------
@dataclass(slots=True)
class ChatMessage:
    role: str
    content: str
    citations: list = field(default_factory=list)
    suggestions: list = field(default_factory=list)


@dataclass(slots=True)
class Choice:
    index: int
    message: ChatMessage
    finish_reason: str | None = "stop"


@dataclass(slots=True)
class ChatCompletion:
    id: str
    model: str
    choices: list
    created: int
    object: str = "chat.completion"
    usage: Any = None
    conversation_id: str | None = None
    title: str | None = None
    throttling: Any = None

    @property
    def content(self) -> str:
        return self.choices[0].message.content if self.choices else ""


@dataclass(slots=True)
class ChoiceDelta:
    role: str | None = None
    content: str | None = None


@dataclass(slots=True)
class ChunkChoice:
    index: int
    delta: ChoiceDelta
    finish_reason: str | None = None


@dataclass(slots=True)
class ChatCompletionChunk:
    id: str
    model: str
    choices: list
    created: int
    object: str = "chat.completion.chunk"


class ModelsPage:
    """OpenAI-like page: iterable, plus `.data`."""

    object = "list"

    def __init__(self, data: list[ModelInfo]):
        self.data = list(data)

    def __iter__(self) -> Iterator[ModelInfo]:
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, i):
        return self.data[i]


# ---- mapping helpers -------------------------------------------------------
_ALIASES = {
    "auto": Model.AUTO, "fast": Model.FAST, "quick": Model.FAST,
    "think": Model.THINK, "reasoning": Model.THINK, "smart": Model.AUTO,
    "research": Model.RESEARCH, "deep-research": Model.RESEARCH,
}


def _map_model(model: str | Model):
    if isinstance(model, Model):
        return model
    if isinstance(model, str):
        return _ALIASES.get(model.lower(), model)  # raw id passes through
    return Model.AUTO


def _model_str(model: str | Model) -> str:
    return model.value if isinstance(model, Model) else str(model)


def _turn(messages: list[dict]) -> str:
    """Collapse OpenAI `messages[]` into one Copilot turn: the last user message,
    with any system content prepended as context."""
    if not messages:
        raise BadRequestError("messages must be a non-empty list")
    systems: list[str] = []
    last_user: str | None = None
    for m in messages:
        content = m.get("content")
        text = content if isinstance(content, str) else "".join(
            p.get("text", "") for p in (content or []) if isinstance(p, dict)
        )
        if m.get("role") == "system":
            systems.append(text)
        elif m.get("role") == "user":
            last_user = text
    if last_user is None:
        raise BadRequestError("messages must contain a user message")
    return f"[system] {' '.join(systems)}\n\n{last_user}" if systems else last_user


def _completion(final: Final, model: str | Model) -> ChatCompletion:
    msg = ChatMessage(
        role="assistant", content=final.text or "",
        citations=list(final.citations),
        suggestions=[s.text for s in final.suggestions],
    )
    return ChatCompletion(
        id=f"chatcmpl-{uuid.uuid4().hex[:24]}", model=_model_str(model),
        created=int(time.time()), choices=[Choice(0, msg, "stop")],
        conversation_id=final.conversation_id, title=final.title,
        throttling=final.throttling,
    )


def _chunk(model, *, content=None, role=None, finish_reason=None) -> ChatCompletionChunk:
    return ChatCompletionChunk(
        id=f"chatcmpl-{uuid.uuid4().hex[:24]}", model=_model_str(model),
        created=int(time.time()),
        choices=[ChunkChoice(0, ChoiceDelta(role=role, content=content), finish_reason)],
    )


def _to_model(m: ModelInfo) -> ModelInfo:
    return m  # ModelInfo already exposes `.id/.title/.reasoning/.default`


def _make_credential(edition, api_key, credential, token_param) -> Credential:
    if credential is not None:
        return credential
    if api_key is None:
        return Anonymous()
    param = token_param or ("accessToken" if edition == "consumer" else "access_token")
    return QueryToken(api_key, param=param)


# ---- async client ----------------------------------------------------------
class _AsyncCompletions:
    def __init__(self, client: AsyncCopilot):
        self._client = client

    async def create(self, *, messages, model="auto", stream=False, conversation=None, **_ignored):
        if stream:
            return _AsyncStream(self._agen(model, messages, conversation))
        conv = await self._client._resolve_conv(conversation)
        final = await self._client._core.ask_text(
            _turn(messages), conversation=conv, model=_map_model(model)
        )
        return _completion(final, model)

    def _agen(self, model, messages, conversation):
        async def gen():
            conv = await self._client._resolve_conv(conversation)
            role: str | None = "assistant"
            async for ev in self._client._core.ask(
                _turn(messages), conversation=conv, model=_map_model(model)
            ):
                if isinstance(ev, Delta):
                    yield _chunk(model, content=ev.text, role=role)
                    role = None
                elif isinstance(ev, Final):
                    yield _chunk(model, finish_reason="stop")
        return gen()


class _AsyncStream:
    def __init__(self, agen):
        self._agen = agen

    def __aiter__(self):
        return self._agen

    async def __anext__(self):
        return await self._agen.__anext__()


class _AsyncChat:
    def __init__(self, client: AsyncCopilot):
        self.completions = _AsyncCompletions(client)


class _AsyncModels:
    def __init__(self, client: AsyncCopilot):
        self._client = client

    async def list(self) -> ModelsPage:
        return ModelsPage([_to_model(m) for m in await self._client._core.list_models()])

    async def retrieve(self, model: str) -> ModelInfo:
        for m in await self._client._core.list_models():
            if m.id == model:
                return m
        raise BadRequestError(f"unknown model {model!r}")


class AsyncCopilot:
    """Async client, mirrors `openai.AsyncOpenAI`."""

    def __init__(
        self, *, edition="m365", api_key=None, conversation=None, credential=None,
        token_param=None, transport_factory=WebsocketsTransport, http=None,
    ):
        self._core = CopilotClient(
            edition, _make_credential(edition, api_key, credential, token_param),
            transport_factory=transport_factory, http=http,
        )
        self._conversation = conversation
        self.chat = _AsyncChat(self)
        self.models = _AsyncModels(self)

    async def _resolve_conv(self, conversation) -> ConversationRef:
        conv = conversation if conversation is not None else self._conversation
        if conv is None:
            conv = await self._core.create_conversation()
        return ConversationRef(id=conv) if isinstance(conv, str) else conv

    async def aclose(self) -> None:
        return None


# ---- sync client (background event loop) -----------------------------------
class _Loop:
    def __init__(self):
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="copilot-sdk", daemon=True)
        self._thread.start()

    def run(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def stream(self, agen_factory) -> Iterator:
        q: queue.Queue = queue.Queue(maxsize=64)

        async def pump():
            try:
                async for x in agen_factory():
                    q.put(("item", x))
                q.put(("done", None))
            except Exception as e:
                q.put(("err", e))

        asyncio.run_coroutine_threadsafe(pump(), self._loop)
        while True:
            kind, val = q.get()
            if kind == "item":
                yield val
            elif kind == "done":
                return
            else:
                raise val

    def close(self):
        self._loop.call_soon_threadsafe(self._loop.stop)


class _SyncCompletions:
    def __init__(self, client: Copilot):
        self._client = client

    def create(self, *, messages, model="auto", stream=False, conversation=None, **_ignored):
        c = self._client
        if stream:
            return c._loop.stream(
                lambda: c._async.chat.completions._agen(model, messages, conversation)
            )
        return c._loop.run(
            c._async.chat.completions.create(
                messages=messages, model=model, stream=False, conversation=conversation
            )
        )


class _SyncChat:
    def __init__(self, client: Copilot):
        self.completions = _SyncCompletions(client)


class _SyncModels:
    def __init__(self, client: Copilot):
        self._client = client

    def list(self) -> ModelsPage:
        return self._client._loop.run(self._client._async.models.list())

    def retrieve(self, model: str) -> ModelInfo:
        return self._client._loop.run(self._client._async.models.retrieve(model))


class Copilot:
    """Sync client, mirrors `openai.OpenAI`. Wraps the async engine on a
    background event loop."""

    def __init__(self, **kwargs):
        self._loop = _Loop()
        self._async = AsyncCopilot(**kwargs)
        self.chat = _SyncChat(self)
        self.models = _SyncModels(self)

    def close(self):
        self._loop.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
