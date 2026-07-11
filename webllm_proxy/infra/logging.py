"""Logging setup + the one place any request/response debug artifact gets
written. Each provider used to hand-roll its own hardcoded `/tmp/*.json` dump
(Linux-only path, no redaction); `dump_exchange` replaces both -- see
docs/refactor/PROGRESS.md."""

import contextvars
import json
import logging
import tempfile
import uuid
from pathlib import Path

from .env import env_str
from .redaction import redact

log = logging.getLogger(__name__)

_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def new_correlation_id() -> str:
    """Start a new correlation id for the current context (e.g. one research
    job) and return it. Threads/async tasks each get their own via contextvars."""
    cid = uuid.uuid4().hex[:12]
    _correlation_id.set(cid)
    return cid


def correlation_id() -> str | None:
    return _correlation_id.get()


def dump_exchange(name: str, payload: dict, *, enabled: bool) -> None:
    """Write a redacted JSON snapshot of one request/response exchange for
    debugging, gated by `enabled` (a provider's `*_DEBUG_DUMP` flag). Lands in
    the OS temp dir (override with `WEBLLM_PROXY_DUMP_DIR`), never the repo;
    every value passes through `redact()` first. Best-effort: a dump failure
    must never break the request it's describing."""
    if not enabled:
        return
    try:
        base = Path(env_str("WEBLLM_PROXY_DUMP_DIR") or tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        text = json.dumps(redact(payload), indent=2, default=str)[:400_000]
        (base / f"{name}_last_request.json").write_text(text)
    except Exception:
        log.debug("dump_exchange(%s) failed", name, exc_info=True)
