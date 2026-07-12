"""Tiny stdlib-only HTTP client for the gateway: fetch small JSON bodies and
open streaming (SSE) forwards to the per-provider proxies. No third-party
dependency -- mirrors `cli.py`'s urllib usage. The gateway forwards bytes but
never logs them, so no secret passes through a logger here."""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from typing import Any

from ..infra.http_direct import urlopen as _urlopen


def _load(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return None


def _status(resp: Any) -> int:
    return getattr(resp, "status", None) or getattr(resp, "code", 200)


def get_json(url: str, timeout: float = 10.0) -> tuple[int, Any]:
    """GET a small JSON body -> (status, parsed). Raises `urllib.error.URLError`
    if the host is unreachable; an HTTP >=400 (an `HTTPError`, itself readable)
    is returned as a normal (status, body) result."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with _urlopen(req, timeout=timeout) as resp:
            return _status(resp), _load(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _load(e.read())


def open_forward(
    method: str,
    url: str,
    *,
    data: bytes | None = None,
    headers: dict | None = None,
    timeout: float = 300.0,
) -> Any:
    """Open an upstream request and return the raw response (an
    `http.client.HTTPResponse`, or an `HTTPError` -- itself a readable response
    for >=400). Raises `urllib.error.URLError` if the host is unreachable."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        return _urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        return e


def iter_response(resp: Any) -> Iterator[bytes]:
    """Yield the upstream body as it arrives (line-framed, which preserves SSE
    `data: ...` events), then close."""
    try:
        yield from resp
    finally:
        with contextlib.suppress(Exception):
            resp.close()


def status_of(resp: Any) -> int:
    return _status(resp)


def content_type_of(resp: Any, default: str = "application/json") -> str:
    try:
        return resp.headers.get("Content-Type", default) or default
    except Exception:
        return default
