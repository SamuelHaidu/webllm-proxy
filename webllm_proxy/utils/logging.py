"""Logging setup + the one place a request/response debug artifact gets written
(redacted, gated, into the OS temp dir -- never the repo)."""

import json
import logging
import tempfile
from pathlib import Path

from .env import env_str
from .redaction import redact

log = logging.getLogger(__name__)


def configure_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def dump_exchange(name: str, payload: dict, *, enabled: bool) -> None:
    """Write a redacted JSON snapshot of one exchange for debugging, gated by
    `enabled`. Lands in the OS temp dir (override `WEBLLM_PROXY_DUMP_DIR`);
    every value passes through `redact()`. Best-effort."""
    if not enabled:
        return
    try:
        base = Path(env_str("WEBLLM_PROXY_DUMP_DIR") or tempfile.gettempdir())
        base.mkdir(parents=True, exist_ok=True)
        text = json.dumps(redact(payload), indent=2, default=str)[:400_000]
        (base / f"{name}_last_request.json").write_text(text, encoding="utf-8")
    except Exception:
        log.debug("dump_exchange(%s) failed", name, exc_info=True)
