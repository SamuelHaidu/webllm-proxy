"""Token usage estimation: real BPE counts + a small per-provider chat-format
overhead model, ported from `coder/ai-tokenizer` (MIT) -- see
`tokens_data/ATTRIBUTION.md` for what was vendored and why, and the method.

No upstream call, no API key: `tiktoken` supplies the OpenAI BPE vocabs
(`o200k_base`/`cl100k_base`/`p50k_base`) natively; the one non-OpenAI vocab
(`claude`, Anthropic's last publicly documented tokenizer -- Claude 3+ has no
public one) is vendored as `tokens_data/claude_encoding.json`. Every provider
picks one representative overhead profile from `tokens_data/model_profiles.json`
(no per-model-slug mapping -- see ATTRIBUTION.md) since wire model ids don't
line up with upstream's own naming.

This is an *estimate*, same caveat upstream states (~95-99% accuracy
depending on model) -- good enough for a `usage` field, not a source of truth.

**Which profile applies is configurable at two levels** (`webllm-proxy.yaml`,
validated against `available_profiles()` at config load -- see `utils.config`):
a per-provider default (`providers.<name>.tokenizer`) and, when one provider's
models don't all use the same underlying model family, a per-model override
(`providers.<name>.models.<slug>.tokenizer`, e.g. one databricks model routed
to Claude and another to GPT). Default is `openai/gpt-5` for every provider
with no per-model overrides (no guessing baked in); `server.serve()` applies
the YAML values once at boot via `configure_profiles()` (provider-level) and
`configure_model_profiles()` (model-level, wins when both are set).
"""

from __future__ import annotations

import base64
import json
import logging
from functools import lru_cache
from pathlib import Path

import tiktoken

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).parent / "tokens_data"

# Default: every provider counts as if it were OpenAI's current flagship,
# until `configure_profiles()` (the YAML `tokenizer:` fields) says otherwise.
_FALLBACK_PROFILE = "openai/gpt-5"
_provider_profile: dict[str, str] = {
    "chatgpt": _FALLBACK_PROFILE,
    "copilot": _FALLBACK_PROFILE,
    "databricks": _FALLBACK_PROFILE,
}
# Per-model overrides, keyed by the full wire id ("databricks__claude-4-5-sonnet")
# -- wins over `_provider_profile` for that one model. Empty until
# `configure_model_profiles()` (the YAML `models.<slug>.tokenizer` fields) sets
# some.
_model_profile: dict[str, str] = {}


@lru_cache(maxsize=1)
def _profiles() -> dict:
    with (_DATA_DIR / "model_profiles.json").open() as f:
        return json.load(f)


def available_profiles() -> list[str]:
    """Every vendored profile key (`<provider>/<slug>`, e.g. `openai/gpt-5`,
    `anthropic/claude-sonnet-4.5`) a YAML `tokenizer:` field can reference."""
    return sorted(_profiles())


def configure_profiles(overrides: dict[str, str]) -> None:
    """Set which vendored profile each provider's usage estimate uses *by
    default* (called once at server boot from
    `utils.config.Config.tokenizer_profiles()`). Unknown provider names are
    ignored; an unknown profile key for a known provider is logged and
    skipped (keeps whatever was set before) rather than crashing a running
    server -- `utils.config` already validates against `available_profiles()`
    at YAML load time, so this should never trigger outside of a
    stale/hand-built config dict."""
    profiles = _profiles()
    for provider, key in overrides.items():
        if provider not in _provider_profile:
            continue
        if key in profiles:
            _provider_profile[provider] = key
        else:
            log.warning(
                "tokens: unknown tokenizer profile %r for provider %r, keeping %r",
                key,
                provider,
                _provider_profile[provider],
            )


def configure_model_profiles(overrides: dict[str, str]) -> None:
    """Set a per-model profile override, keyed by full wire id
    (`"databricks__claude-4-5-sonnet"`) -- wins over the model's provider-level
    default for that one model. Called once at server boot from
    `utils.config.Config.model_tokenizer_overrides()`. An unknown profile key
    is logged and skipped, same as `configure_profiles()`."""
    profiles = _profiles()
    for model_id, key in overrides.items():
        if key in profiles:
            _model_profile[model_id] = key
        else:
            log.warning(
                "tokens: unknown tokenizer profile %r for model %r, ignoring", key, model_id
            )


def _resolve_profile(model_id: str | None) -> dict:
    """Wire model id (`databricks__claude-4-5-sonnet`) -> the configured
    `{encoding, tokens}` profile: an exact per-model override if one was
    configured, else the model's provider-level default, else
    `_FALLBACK_PROFILE`. Never raises."""
    profiles = _profiles()
    if model_id in _model_profile:
        key = _model_profile[model_id]
    else:
        provider = (model_id or "").split("__", 1)[0]
        key = _provider_profile.get(provider, _FALLBACK_PROFILE)
    return profiles.get(key) or profiles[_FALLBACK_PROFILE]


@lru_cache(maxsize=1)
def _claude_encoding() -> tiktoken.Encoding:
    with (_DATA_DIR / "claude_encoding.json").open() as f:
        data = json.load(f)
    ranks: dict[bytes, int] = {}
    for line in data["bpe_ranks"].split("\n"):
        if not line:
            continue
        _hash, offset_str, *tokens = line.split(" ")
        offset = int(offset_str)
        for i, tok in enumerate(tokens):
            ranks[base64.b64decode(tok)] = offset + i
    return tiktoken.Encoding(
        name="claude",
        pat_str=data["pat_str"],
        mergeable_ranks=ranks,
        special_tokens=dict(data["special_tokens"]),
    )


@lru_cache(maxsize=8)
def _encoding_for(name: str) -> tiktoken.Encoding:
    if name == "claude":
        return _claude_encoding()
    return tiktoken.get_encoding(name)  # o200k_base / cl100k_base / p50k_base


def count_text(text: str, model_id: str | None = None) -> int:
    """Raw BPE token count of one string, using the encoding the model's
    provider is expected to use."""
    if not text:
        return 0
    profile = _resolve_profile(model_id)
    enc = _encoding_for(profile["encoding"])
    return len(enc.encode(text, disallowed_special=()))


def _prop_tokens(enc: tiktoken.Encoding, prop: dict, cfg: dict) -> int:
    """Tokens for one property's description/enum/nested-object/array-of-objects
    overhead (everything but its name, which the caller already counted)."""
    total = 0
    desc = prop.get("description")
    if desc:
        total += cfg["perPropDesc"] + len(enc.encode(desc))
    enum_vals = prop.get("enum")
    if isinstance(enum_vals, list):
        total += cfg["perEnum"]
        total += sum(len(enc.encode(str(v))) for v in enum_vals)
    ptype = prop.get("type")
    if ptype == "object":
        total += cfg.get("perNestedObject", 0) + _schema_tokens(enc, prop, cfg)
    elif ptype == "array":
        items = prop.get("items")
        if isinstance(items, dict) and items.get("type") == "object":
            total += cfg.get("perArrayOfObjects", 0) + _schema_tokens(enc, items, cfg)
    return total


def _schema_tokens(enc: tiktoken.Encoding, schema: dict, cfg: dict) -> int:
    """Walk a JSON-Schema `parameters`/`inputSchema` object (not Zod -- this
    project's tool defs are plain JSON Schema), counting property names,
    descriptions, and enum values with the same per-field overhead constants
    upstream fits per model."""
    props = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(props, dict):
        return 0
    total = 0
    for i, (key, prop) in enumerate(props.items()):
        total += len(enc.encode(str(key)))
        total += cfg["perFirstProp"] if i == 0 else cfg["perAdditionalProp"]
        if isinstance(prop, dict):
            total += _prop_tokens(enc, prop, cfg)
    return total


def _tools_tokens(enc: tiktoken.Encoding, tools: list[dict] | None, cfg: dict) -> int:
    if not tools:
        return 0
    total = cfg["toolsExist"]
    for i, t in enumerate(tools):
        fn = (t.get("function") or {}) if isinstance(t, dict) else {}
        total += len(enc.encode(fn.get("name") or ""))
        desc = fn.get("description")
        if desc:
            total += cfg["perDesc"] + len(enc.encode(desc))
        params = fn.get("parameters")
        if isinstance(params, dict):
            total += _schema_tokens(enc, params, cfg)
        if i > 0:
            total += cfg["perTool"]
    return total


def _message_tokens(enc: tiktoken.Encoding, m: dict, cfg: dict) -> int:
    mult = cfg["contentMultiplier"]
    total = cfg["perMessage"] + len(enc.encode(m.get("role") or ""))
    content = m.get("content")
    if isinstance(content, str) and content:
        total += round(len(enc.encode(content)) * mult)
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                total += round(len(enc.encode(part.get("text") or "")) * mult)
    for tc in m.get("tool_calls") or []:
        fn = tc.get("function") or {}
        piece = (fn.get("name") or "") + (fn.get("arguments") or "")
        total += round(len(enc.encode(piece)) * mult)
    return total


def estimate_prompt_tokens(
    messages: list[dict], tools: list[dict] | None, model_id: str | None = None
) -> int:
    """Prompt-side token estimate: BPE-encode every message/tool field and add
    the provider's small fitted chat-format overhead on top (mirrors OpenAI's
    own documented per-message wrapping-token overhead, generalized)."""
    profile = _resolve_profile(model_id)
    enc = _encoding_for(profile["encoding"])
    cfg = profile["tokens"]
    total = cfg["baseOverhead"]
    total += sum(_message_tokens(enc, m, cfg) for m in messages or [])
    total += _tools_tokens(enc, tools, cfg)
    return max(total, 0)


def estimate_completion_tokens(text: str, model_id: str | None = None) -> int:
    """Completion-side token estimate: just a raw BPE count (no chat-format
    overhead -- that's already counted once on the prompt side)."""
    return count_text(text, model_id)


def usage(prompt: int = 0, completion: int = 0) -> dict:
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def estimate_usage(
    messages: list[dict],
    tools: list[dict] | None,
    completion_text: str,
    model_id: str | None = None,
) -> dict:
    """Convenience: the full `usage` wire object for one turn, estimated from
    the request messages/tools and the assembled reply text."""
    return usage(
        estimate_prompt_tokens(messages, tools, model_id),
        estimate_completion_tokens(completion_text, model_id),
    )
