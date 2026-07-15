# 2026-07-15 — Databricks Claude channel: OpenAI→Anthropic parameter forwarding review

Review of what `utils.convert.openai_to_anthropic` (the databricks Claude
channel's request converter) actually forwards, against Anthropic's **current**
Messages API spec, prompted by "are all OpenAI params — especially thinking /
adaptive thinking — being sent correctly?". Not a browser session; a spec audit
+ code fix. Live verification is still open (no `DATABRICKS_PROXY_URL` this
session) — see the risks at the bottom.

## Spec sources (fetched live)

- **Adaptive thinking** — `platform.claude.com/docs/en/build-with-claude/adaptive-thinking`
- **Extended thinking** — `platform.claude.com/docs/en/build-with-claude/extended-thinking`
- (effort/tool-choice constraints cross-checked against the Bedrock "extended
  thinking" + "adaptive thinking" mirror pages)

### What the spec says that matters here

- **Adaptive thinking** (`thinking:{type:"adaptive"}` + top-level
  `output_config:{effort:"low|medium|high|max|xhigh"}`, model decides depth) is
  the mode on **Sonnet >= 4.6 / Sonnet 5 / Opus >= 4.6 / Fable+Mythos 5**. On
  Sonnet 5 / Opus 4.7+ manual `{type:"enabled"}` is **rejected with a 400**.
- **Claude Sonnet 4.5 and Opus 4.5 do NOT support adaptive** — they require
  manual `{type:"enabled", budget_tokens:N}`. Sonnet 4.5 is the **only**
  entitlement-enabled model on the databricks `editor-assistant-agent-mode`
  channel (every Opus / Sonnet-4.6+ / Sonnet-5 is `MODEL_DISABLED` — see
  `2026-07-10-databricks-llmproxy.md`). So on databricks **today, adaptive is
  unreachable**; manual thinking is the only live path.
- With **manual thinking enabled**: `temperature` may only be `1`; `top_p`/
  `top_k` overrides are rejected; **forced tool use** (`tool_choice` `any` or a
  named tool) is **not allowed** — only `auto`/`none`. `budget_tokens` minimum
  is **1024**, and `max_tokens` must be **>** `budget_tokens`.
- **Interleaved thinking** (think between tool calls) is the beta
  `interleaved-thinking-2025-05-14`; the real Genie client sends it as a body
  field alongside manual thinking (HAR, `2026-07-10-databricks-llmproxy.md`).
  Adaptive thinking auto-enables it (no beta needed).

## What the converter did wrong (all fixed)

Before, `openai_to_anthropic` forwarded only `messages`, `max_tokens`, `tools`,
`tool_choice` (required/specific only), `temperature`, and a `thinking` budget.
On this channel an invalid field is the signature **empty-body HTTP 400** (cf.
`eager_input_streaming`), so these were latent request failures, not cosmetics:

| Bug | Fix (`utils/convert.py`) |
|---|---|
| sent `temperature` (and never `top_p`) alongside thinking | drop `temperature`/`top_p` when thinking is on (`_apply_sampling`) |
| mapped `tool_choice` required/specific → forced `any`/`tool` even with thinking | downgrade forced choice → `auto` when thinking is on (`_resolve_tool_choice`) |
| `budget = min(effort, max_tokens-1)`, no 1024 floor | floor 1024; keep `max_tokens > budget`, raising `max_tokens` only if a tiny client cap can't fit the minimum (`_apply_thinking`) |
| never sent the interleaved beta | `build_llmproxy_envelope` adds `anthropic_beta:[interleaved-thinking-2025-05-14]` when `thinking.type=="enabled"` |

## What wasn't being forwarded (now is)

Every OpenAI field with an Anthropic equivalent:

| OpenAI | Anthropic | Note |
|---|---|---|
| `max_completion_tokens` | `max_tokens` | fallback when `max_tokens` absent (newer clients) |
| `stop` (str \| list) | `stop_sequences` (list) | `_stop_sequences` normalizes |
| `top_p` | `top_p` | only when thinking off |
| `user` | `metadata.user_id` | |
| `parallel_tool_calls:false` | `tool_choice.disable_parallel_tool_use:true` | merged onto the choice |
| `tool_choice:"none"/"auto"` | `{type:"none"}` / `{type:"auto"}` | were dropped before |
| `reasoning_effort` | `thinking` (+ `output_config.effort` on adaptive) | via `effort`, model-aware |

Intentionally **not** forwarded (no Anthropic equivalent): `n`,
`presence_penalty`, `frequency_penalty`, `logit_bias`, `logprobs`, `seed`,
`response_format`, `stream_options`.

## Adaptive thinking: model-aware, dormant

`_supports_adaptive_thinking(model)` recognizes adaptive-capable Claudes in
**both** naming styles (databricks `claude-4-6-sonnet`, Anthropic
`claude-sonnet-4-6`): Sonnet >= 4.6 (incl. Sonnet 5), Opus >= 4.6, Fable/Mythos
5. Those get `{type:"adaptive", display:"summarized"}` + `output_config.effort`
(`min→low, standard→medium, extended→high, max→max`). **Default is manual** — an
unrecognized/older model (incl. Sonnet 4.5 and `None`) uses
`{type:"enabled", budget_tokens}`. This is correct for the only enabled model
*and* the safe choice against the unknown-field 400. `display:"summarized"` is
set because it defaults to `"omitted"` (empty thinking text) on the newest
models.

## Response side: signature captured, round-trip is a wire limitation

`AnthropicSSE` now captures the streamed thinking-block `signature`
(`signature_delta`) into `self.signatures` instead of dropping it. A **full**
round-trip (passing signed thinking blocks back on the next tool turn, which
the spec requires for tool-use-with-thinking) is **not achievable over the
OpenAI Chat Completions wire** — it has no field to carry an opaque Anthropic
signature. In practice Sonnet 4.5 tolerates missing prior thinking blocks
(earlier Sonnet "strips them" by default per the spec, and the 2026-07-10 e2e
pi run drove a 9-tool-turn loop with thinking on and no signatures echoed), so
this is captured-for-observability, documented as a known limitation, not
forced.

## Open — live verification (deferred; needs `DATABRICKS_PROXY_URL` + login)

All fixes are unit-tested (`tests/test_convert.py`, `tests/test_databricks.py`),
but the channel's field allowlist is only knowable live. Risk order (each, if it
trips the empty-body 400, is a one-line drop like `_DROP_TOOL_FIELDS`):

1. `output_config` (adaptive) — dormant anyway (no adaptive model enabled).
2. `metadata` — standard Anthropic field, not seen in the Genie HAR.
3. `stop_sequences` / `top_p` — standard Anthropic fields, not in the HAR.
4. `anthropic_beta` — **HAR-proven accepted** (the real Genie client sends it).

Suggested check once a workspace is available (via `scripts/e2e_live.py` or
`scripts/dbx_models_probe.py`): one Claude request with `reasoning_effort:"high"`
+ `temperature:0.2` + a client tool — should now 200 with streamed
`thinking`/`tool_use` where the stray `temperature` previously tripped the 400.
