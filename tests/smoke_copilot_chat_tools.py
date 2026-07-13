"""Live smoke for Copilot's live model discovery + plain chat + emulated tool
calling (the `<tool>` tag contract -- copilot has no native function-calling
channel). Kept deliberately tiny: Copilot throttles at the request layer after
~20 rapid programmatic turns (see `docs/discovery/2026-07-11-ms365-copilot-sydney.md`,
Update 3), so this sends exactly TWO chat turns total (one plain, one tool-call),
sharing one session with `smoke_copilot_reasoning.py`'s two turns if run together
-- don't chain every copilot smoke file back-to-back repeatedly.

Opt-in only (double-skipped otherwise):
  - `WEBLLM_PROXY_COPILOT_LIVE=1`         -- explicit gate (never runs in `poe check`)
  - a logged-in copilot profile           -- else `webllm-proxy login --provider copilot`

Run:
    WEBLLM_PROXY_COPILOT_LIVE=1 uv run pytest tests/smoke_copilot_chat_tools.py -v
"""

import json
import os

import pytest

from webllm_proxy.providers import build_provider
from webllm_proxy.utils.config import Config, CopilotConfig, ProvidersConfig
from webllm_proxy.utils.openai import split_model

_GATE = "WEBLLM_PROXY_COPILOT_LIVE"
# The manifest's own id for the fast, no-thinking tone -- a literal test target,
# not a provider heuristic (see providers/copilot/models.py).
_CHAT_ID = "Chat"

_WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string", "description": "City name"}},
            "required": ["city"],
        },
    },
}


def _config() -> Config:
    headless = os.environ.get("WEBLLM_PROXY_COPILOT_HEADLESS", "1") != "0"
    return Config(providers=ProvidersConfig(copilot=CopilotConfig(enabled=True, headless=headless)))


def _logged_in() -> bool:
    return (_config().profile_dir("copilot") / "Default" / "Cookies").exists()


pytestmark = [
    pytest.mark.skipif(
        not os.environ.get(_GATE),
        reason=f"set {_GATE}=1 to run the live copilot chat/tools smoke",
    ),
    pytest.mark.skipif(
        not _logged_in(),
        reason="copilot profile not logged in (run: webllm-proxy login --provider copilot)",
    ),
]


@pytest.fixture(scope="module")
def provider():
    """One headless copilot session shared by discovery + both chat turns."""
    prov = build_provider("copilot", _config())
    prov.start()
    if not prov.wait_ready(timeout=150):
        prov.close()
        pytest.skip(f"copilot session not ready: {prov.error or 'timeout'}")
    try:
        yield prov
    finally:
        prov.close()


def test_model_discovery_is_live_not_static(provider):
    """No hardcoded list: every id comes back namespaced, and there's more than
    one distinct option (a single-entry fallback would suggest discovery failed
    silently and something papered over it)."""
    listed = provider.models()
    assert listed, "discovery returned nothing"
    ids = [split_model(m["id"])[1] for m in listed]
    assert len(set(ids)) > 1, f"expected multiple discovered models, got {ids}"
    assert all(m["id"].startswith("copilot__") for m in listed)
    assert _CHAT_ID in ids, f"{_CHAT_ID!r} not in live-discovered models: {ids}"


def test_chat_quick_response(provider):
    resp = provider.completions(
        {
            "model": _CHAT_ID,
            "messages": [{"role": "user", "content": "Reply with exactly the single word: pong"}],
            "stream": False,
        }
    )
    assert isinstance(resp, dict), resp
    assert "error" not in resp, resp.get("error")
    content = resp["choices"][0]["message"]["content"] or ""
    assert content.strip(), "empty answer from copilot chat"


@pytest.mark.xfail(
    reason=(
        "M365 Copilot's own alignment refuses externally-declared <tool> schemas: "
        "confirmed live (2026-07-13) across 4 variants (get_weather + a fictitious "
        "lookup_internal_ticket, both the default and a softened/strengthened "
        "contract prompt) -- it either states the tool 'isn't actually available' "
        "or, when it has a real equivalent (web search), just answers directly and "
        "ignores the tag protocol. Not a wording bug; see "
        "docs/discovery/2026-07-13-copilot-live-test.md. xfail (not skip) so this "
        "flips to XPASS and gets noticed if Microsoft's tuning or our prompt ever "
        "changes that."
    ),
    strict=False,
)
def test_emulated_tool_call(provider):
    resp = provider.completions(
        {
            "model": _CHAT_ID,
            "messages": [{"role": "user", "content": "Use the get_weather tool for Paris."}],
            "tools": [_WEATHER_TOOL],
            "tool_choice": "required",
            "stream": False,
        }
    )
    assert isinstance(resp, dict), resp
    assert "error" not in resp, resp.get("error")
    msg = resp["choices"][0]["message"]
    calls = msg.get("tool_calls")
    assert calls, f"no tool_calls emulated by copilot: {msg}"
    assert calls[0]["function"]["name"] == "get_weather"
    args = json.loads(calls[0]["function"]["arguments"] or "{}")
    assert "city" in args, f"missing 'city' arg: {args}"
