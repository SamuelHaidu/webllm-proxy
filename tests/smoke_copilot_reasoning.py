"""Live smoke for the Copilot deep-thinking model, kept deliberately tiny.

Copilot throttles at the request layer after ~20 rapid programmatic turns (see
`docs/discovery/2026-07-11-ms365-copilot-sydney.md`, Update 3), so this is NOT
part of the parametrized `smoke_openai_sdk.py` battery. It boots ONLY the copilot
provider against the already-logged-in profile and sends exactly TWO turns to the
deep-thinking model (`copilot__Reasoning`, "Think Deeper"): one non-streaming, one
streaming. Nothing else -- keep it that way to avoid getting throttled.

Opt-in only (double-skipped otherwise):
  - `WEBLLM_PROXY_COPILOT_LIVE=1`         -- explicit gate (never runs in `poe check`)
  - a logged-in copilot profile           -- else `webllm-proxy login --provider copilot`

Run:
    WEBLLM_PROXY_COPILOT_LIVE=1 uv run pytest tests/smoke_copilot_reasoning.py -v
    # headed browser for debugging: also set WEBLLM_PROXY_COPILOT_HEADLESS=0

Note: the provider drives the page composer at its default tone; it does not (yet)
click the model selector, so this confirms the `copilot__Reasoning` id round-trips
end to end and returns a real answer -- it does not assert the UI switched to
"Think Deeper".
"""

import json
import os

import pytest

from webllm_proxy.providers import build_provider
from webllm_proxy.utils.config import Config, CopilotConfig, ProvidersConfig
from webllm_proxy.utils.openai import split_model

# The manifest's own id for the deep-thinking option (see
# providers/copilot/models.py) -- a literal test target, not a provider heuristic.
_REASONING_ID = "Reasoning"

_GATE = "WEBLLM_PROXY_COPILOT_LIVE"

# A deterministic reasoning task: any competent model ends on 391 (17 * 23).
_PROMPT = "What is 17 * 23? Think it through step by step, then end with the final number."


def _config() -> Config:
    headless = os.environ.get("WEBLLM_PROXY_COPILOT_HEADLESS", "1") != "0"
    return Config(providers=ProvidersConfig(copilot=CopilotConfig(enabled=True, headless=headless)))


def _logged_in() -> bool:
    return (_config().profile_dir("copilot") / "Default" / "Cookies").exists()


pytestmark = [
    pytest.mark.skipif(
        not os.environ.get(_GATE),
        reason=f"set {_GATE}=1 to run the live copilot deep-thinking smoke",
    ),
    pytest.mark.skipif(
        not _logged_in(),
        reason="copilot profile not logged in (run: webllm-proxy login --provider copilot)",
    ),
]


@pytest.fixture(scope="module")
def provider():
    """One headless copilot session shared by both turns (single boot, two turns)."""
    prov = build_provider("copilot", _config())
    prov.start()
    if not prov.wait_ready(timeout=150):
        prov.close()
        pytest.skip(f"copilot session not ready: {prov.error or 'timeout'}")
    try:
        yield prov
    finally:
        prov.close()


def _reasoning_slug(prov) -> str:
    """The deep-thinking model's slug -- confirmed present in the provider's live
    discovery (skip, don't guess, if the manifest ever drops/renames it)."""
    ids = [split_model(m["id"])[1] for m in prov.models()]
    if _REASONING_ID not in ids:
        pytest.skip(f"{_REASONING_ID!r} not in live-discovered models: {ids}")
    return _REASONING_ID


def test_reasoning_deep_think_nonstream(provider):
    slug = _reasoning_slug(provider)
    resp = provider.completions(
        {"model": slug, "messages": [{"role": "user", "content": _PROMPT}], "stream": False}
    )
    assert isinstance(resp, dict), resp
    assert "error" not in resp, resp.get("error")
    content = resp["choices"][0]["message"]["content"] or ""
    assert content.strip(), "empty answer from copilot deep-thinking model"
    assert "391" in content.replace(",", ""), f"reasoning answer wrong/incomplete: {content!r}"


def test_reasoning_deep_think_stream(provider):
    slug = _reasoning_slug(provider)
    result = provider.completions(
        {"model": slug, "messages": [{"role": "user", "content": _PROMPT}], "stream": True}
    )
    text = ""
    for sse in result:  # fully drain so the provider releases its turn lock
        for raw in str(sse).splitlines():
            line = raw.strip()
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                continue
            for ch in json.loads(data).get("choices") or []:
                text += (ch.get("delta") or {}).get("content") or ""
    assert text.strip(), "no streamed text from copilot deep-thinking model"
