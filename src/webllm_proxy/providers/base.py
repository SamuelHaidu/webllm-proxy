"""The Provider interface and the small value types the browser core exchanges
with providers.

A **Provider** adapts one login-only web LLM backend to this tool. It supplies:

* *config* — where the login profile lives, the URL to open, headless flag;
* *browser hooks* (run on the worker thread) — how to check auth, which network
  response to capture, how to trigger a request, and (optionally) a CDP `Fetch`
  request rewrite;
* an *accumulator* — turns captured raw SSE bytes into client-facing events;
* an *HTTP surface* — registers its API routes on the shared Flask app.

The browser transport in `core/browser.py` is entirely provider-agnostic and
drives everything through this interface, so backends stay decoupled and each
piece (accumulators, request/response mapping) is unit-testable without a
browser.
"""
from __future__ import annotations

import queue
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Iterable

# Events flow browser-core -> server. Shape: (kind, value). Kinds:
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
    read by the core when the transport ends the stream (loadingFinished)."""

    finish_reason: str | None = None

    @abstractmethod
    def feed(self, chunk: str) -> Iterable[Event]:
        ...

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
    queue the Flask handler drains (terminated by a sentinel `None`)."""

    def __init__(self, payload: Any):
        self.payload = payload
        self.out: "queue.Queue" = queue.Queue()


class Provider(ABC):
    """Adapter for one web LLM backend. Instances are stateful (they may cache
    per-account capabilities discovered at boot)."""

    #: short id used on the CLI (`--provider <name>`) and in messages.
    name: str = "base"

    # ---- config ----------------------------------------------------------
    @property
    @abstractmethod
    def profile_dir(self) -> Path:
        ...

    @property
    @abstractmethod
    def nav_url(self) -> str:
        """URL to open on boot/login (also the origin in-page fetches run from)."""

    @property
    @abstractmethod
    def headless(self) -> bool:
        ...

    @property
    @abstractmethod
    def host(self) -> str:
        ...

    @property
    @abstractmethod
    def port(self) -> int:
        ...

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

    def on_ready(self, page) -> None:
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
    def make_accumulator(self) -> Accumulator:
        ...

    # ---- HTTP surface ----------------------------------------------------
    @abstractmethod
    def register_routes(self, app, session) -> None:
        """Register this provider's API routes on the Flask `app`."""

    def banner(self, host: str, port: int) -> list[str]:
        """Lines printed once the server is ready (endpoint hints)."""
        return []
