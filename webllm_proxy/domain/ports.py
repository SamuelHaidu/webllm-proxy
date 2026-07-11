"""The seams ("bricks") the rest of the app is built to swap: a web LLM
backend (`Provider`), a wire-format parser (`Accumulator`), a way to recover
tool calls from a turn (`ToolStrategy`), a prompt source (`PromptStore`), and
(from Phase B) a research engine (`ResearchBackend`) and job storage
(`JobStore`). Protocols are structural (duck-typed, no registration); `Provider`
and `Accumulator` are ABCs because their concrete subclasses share real
inherited behavior (`Job`, `flush()`'s default), not just a shape.

Nothing in this module imports another `webllm_proxy` package -- it sits at
the bottom of the dependency graph.
"""

from __future__ import annotations

import queue
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Protocol

# Events flow transport -> server. Shape: (kind, value). Kinds:
#   ("meta", {"status": int, "content_type": str})   response metadata (first)
#   ("data", str)          raw upstream bytes (pass-through providers)
#   ("content", str)       a chunk of answer text (parsing providers)
#   ("reasoning", str)     a chunk of thinking/reasoning
#   ("tool_call", dict)    a captured native tool call
#   ("error", str)         upstream/transport error
#   ("done", value)        stream finished (value = finish reason or None)
Event = tuple


class Accumulator(ABC):
    """Transforms captured raw stream text into `Event`s. `finish_reason` is
    read by the transport when the stream ends (loadingFinished)."""

    finish_reason: str | None = None

    @abstractmethod
    def feed(self, chunk: str) -> Iterable[Event]: ...

    def flush(self) -> Iterable[Event]:
        return []


class PassthroughAccumulator(Accumulator):
    """Forwards raw stream bytes unchanged as ("data", text). Use when the
    upstream wire format already matches what the client expects (e.g. the
    Databricks Anthropic Messages SSE forwarded to an Anthropic client)."""

    def feed(self, chunk: str) -> Iterable[Event]:
        return [("data", chunk)] if chunk else []


class Job:
    """One request handed to the browser worker. `payload` is provider-specific
    (the provider's browser hooks interpret it); `out` is the per-job event
    queue the caller drains (terminated by a sentinel `None`). `idle_cap_s` /
    `hard_cap_s` bound how long the transport waits for THIS job -- an
    interactive chat turn uses the defaults; a research job passes a much
    longer cap (see `transport/browser.py`)."""

    def __init__(self, payload: Any, *, idle_cap_s: float = 45.0, hard_cap_s: float = 300.0):
        self.payload = payload
        self.idle_cap_s = idle_cap_s
        self.hard_cap_s = hard_cap_s
        self.out: queue.Queue = queue.Queue()


class Provider(ABC):
    """Adapter for one web LLM backend. Instances are stateful (they may cache
    per-account capabilities discovered at boot)."""

    #: short id used on the CLI (`--provider <name>`) and in messages.
    name: str = "base"

    # ---- config ----------------------------------------------------------
    @property
    @abstractmethod
    def profile_dir(self) -> Path: ...

    @property
    @abstractmethod
    def nav_url(self) -> str:
        """URL to open on boot/login (also the origin in-page fetches run from)."""

    @property
    @abstractmethod
    def headless(self) -> bool: ...

    @property
    @abstractmethod
    def host(self) -> str: ...

    @property
    @abstractmethod
    def port(self) -> int: ...

    # ---- browser hooks (worker thread) ----------------------------------
    def fetch_patterns(self) -> list[dict]:
        """CDP `Fetch.enable` patterns. Non-empty => `on_fetch_paused` is wired.
        Default: no request interception."""
        return []

    def on_fetch_paused(self, client, params: dict, job: Job | None) -> None:
        """Handle a paused request (default: continue unchanged). Providers that
        rewrite the outgoing body (e.g. to force a model) override this."""
        client.send("Fetch.continueRequest", {"requestId": params["requestId"]})

    @abstractmethod
    def authed(self, page) -> bool:
        """True if the persisted session is logged in."""

    def on_ready(self, page) -> None:  # noqa: B027 -- intentional default no-op hook
        """Called once after auth, on the worker thread, with the live page.
        Providers may probe per-account capabilities here (use `page.evaluate`
        directly — the worker task loop is not running yet)."""

    @abstractmethod
    def capture_match(self, url: str) -> bool:
        """True for the response whose SSE body should be captured."""

    @abstractmethod
    def trigger(self, page, job: Job) -> None:
        """Start the upstream request for `job` (type into a composer, or issue
        an in-page fetch). Raise to signal a per-request failure."""

    @abstractmethod
    def make_accumulator(self) -> Accumulator: ...

    # ---- HTTP surface ----------------------------------------------------
    @abstractmethod
    def register_routes(self, app, session) -> None:
        """Register this provider's API routes on the Flask `app`."""

    def banner(self, host: str, port: int) -> list[str]:
        """Lines printed once the server is ready (endpoint hints)."""
        return []

    def research_backend(self, session) -> ResearchBackend | None:
        """The research backend for this account, or None if this provider
        doesn't support the research feature at all. Called once at server
        startup (after `session` is ready) -- override to resolve/probe
        capability (e.g. `research.backends.resolve_backend`)."""
        return None


class ToolStrategy(Protocol):
    """One way to recover structured tool calls from a turn's raw output.
    `strategies/tool_calling` composes the known strategies (native-channel
    capture, then the AgentClip tag contract) as an ordered fallback chain."""

    def extract_calls(
        self, content: str, native: list[dict], allowed_names: set[str]
    ) -> tuple[list[dict], str]:
        """-> (openai_tool_calls, leftover_visible_text)."""
        ...


class PromptStore(Protocol):
    """Where prompt text comes from. The default (`prompts.loader`) reads
    `.md` files; anything with this same `get` can stand in (tests, a remote
    config store, ...)."""

    def get(self, name: str, /, **subs: str) -> str: ...


class JobStore(Protocol):
    """Where research jobs (`domain.research.ResearchJob`) are kept between
    the submit and poll HTTP calls. The default (`research.jobstore.memory`)
    is an in-process dict; a persistent store can swap in without touching
    the scheduler or HTTP layer."""

    def put(self, job: Any) -> None: ...
    def get(self, job_id: str) -> Any: ...
    # named list_jobs, not list -- would shadow the builtin `list` this class
    # uses in its own return-type annotation.
    def list_jobs(self) -> list[Any]: ...
    def delete(self, job_id: str) -> None: ...


class ResearchBackend(Protocol):
    """One way to turn a research request (`domain.research.ResearchRequest`)
    into a structured-markdown report (ChatGPT Deep Research vs. the emulated
    web-search-model fallback) -- see `research/backends`. Blocking; the
    research scheduler calls this from its own background thread, never from
    a Flask request thread."""

    name: str

    def available(self, session: Any) -> bool:
        """Whether this backend can run on the given (ready) session/account."""
        ...

    def run(self, request: Any, *, session: Any, on_progress: Any) -> str:
        """-> the markdown report. Calls `on_progress(note: str)` occasionally
        so the caller can surface status while this blocks."""
        ...
