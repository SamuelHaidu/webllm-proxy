"""Live integration check (auto-skips unless a databricks proxy is running):
POST /v1/messages with an extended-`thinking` block via **curl** and assert
thinking content actually streams back.

Guards the fix in docs/discovery/2026-07-10-databricks-llmproxy.md
(Update 2026-07-12): databricks' Claude must be driven with a `thinking` block to
reason, and the llmproxy->Bedrock channel must accept the payload pi sends --
including the newer `thinking.display` sub-field -- rather than rejecting it with
its signature empty-body 400.

Gentle by design: one small request (budget 2048, max_tokens 4096). NOT part of
the offline unit suite -- it needs a real, logged-in, browser-backed proxy, so it
skips cleanly when the proxy is unreachable. Point it elsewhere with
WEBLLM_DATABRICKS_URL / WEBLLM_DATABRICKS_MODEL.
"""

import json
import os
import shutil
import subprocess

import pytest

BASE = os.environ.get("WEBLLM_DATABRICKS_URL", "http://127.0.0.1:5103")
MODEL = os.environ.get("WEBLLM_DATABRICKS_MODEL", "claude-4-5-sonnet")


def _curl(url: str, *args: str, timeout: int = 15) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["curl", "-s", "-m", str(timeout), *args, url],
        capture_output=True,
        text=True,
        check=False,
    )


def _proxy_up() -> bool:
    if shutil.which("curl") is None:
        return False
    r = _curl(f"{BASE}/health", "-o", "/dev/null", "-w", "%{http_code}", timeout=4)
    return r.returncode == 0 and r.stdout.strip() == "200"


pytestmark = pytest.mark.skipif(
    not _proxy_up(), reason=f"no databricks proxy reachable at {BASE} (live test)"
)


def test_messages_streams_thinking_content():
    payload = {
        "model": MODEL,
        "max_tokens": 4096,
        "stream": True,
        # `display` is the residual-risk sub-field: the channel 400s on fields it
        # doesn't recognize, so keep it here to prove it's accepted.
        "thinking": {"type": "enabled", "budget_tokens": 2048, "display": "summarized"},
        "messages": [
            {
                "role": "user",
                "content": "What is 17 * 23? Think through it step by step, then give the number.",
            }
        ],
    }
    r = _curl(
        f"{BASE}/v1/messages",
        "-N",
        "-X",
        "POST",
        "-H",
        "Content-Type: application/json",
        "--data",
        json.dumps(payload),
        timeout=90,
    )
    assert r.returncode == 0, f"curl failed: {r.stderr}"
    body = r.stdout

    # The channel's rejection mode is an empty-body 400; a healthy SSE stream is large.
    assert len(body) > 200, f"suspiciously short response (possible 400): {body!r}"
    # Extended thinking must be genuinely present, not just a fast text-only answer.
    assert '"content_block":{"type":"thinking"' in body, "no thinking content block in the stream"
    assert "thinking_delta" in body, "no thinking_delta chunks streamed"
    # ...and it should still finish cleanly with a normal answer.
    assert "message_stop" in body, "stream did not end with message_stop"
