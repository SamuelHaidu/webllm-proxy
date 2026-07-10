# 2026-07-10 — Reasoning effort: `thinking_effort` ↔ OpenAI `reasoning_effort`

ChatGPT web lets some models run at a chosen "thinking" depth. This is exposed
two ways in the backend:

- **Per-model capability** in `GET /backend-api/models`: each model carries
  `configurable_thinking_effort` (bool) and `thinking_efforts` (the allowed
  values). When configurable, `thinking_efforts` is a list of objects shaped
  `[{"thinking_effort": "<value>"}, …]` (plain strings tolerated too).
- **Per-request selection**: the `POST /backend-api/f/conversation` body carries
  a **root** field `thinking_effort: "<value>"`. It is absent unless a
  thinking-configurable model is selected (our earlier `auto` capture had no
  such key — consistent).

## The web enum

The `thinking_effort` value is a **4-level ladder**:

```
min  <  standard  <  extended  <  max
```

(Confirmed from an enterprise account whose `gpt-5-5` is
`configurable_thinking_effort: true` with those four in `thinking_efforts`.)

**On the personal account used for development, no model is configurable** —
every model returns `configurable_thinking_effort: false` and
`thinking_efforts: []`. So the feature can only be *exercised* against a login
whose plan enables it; here it must be a safe no-op.

Related sibling field seen alongside this: `versions[].intelligence_presets`
in `/backend-api/models` (e.g. `{"title":"Instant","model_slug":"gpt-5-5",
"lane":"instant","preset_type":"available"}`). Not wired into the proxy; noted
for later.

## OpenAI standard we map from

Two surfaces, one enum:

- **Chat Completions** (what this proxy speaks): top-level string
  `reasoning_effort` ∈ `minimal | low | medium | high` (default `medium`;
  `minimal` arrived with GPT-5, `none` with 5.1). Valid only on reasoning models.
- **Responses API**: nested `reasoning: {"effort": "minimal|low|medium|high"}`.

### Mapping (1:1, both ladders are 4 levels)

| OpenAI `reasoning_effort` | web `thinking_effort` |
|---|---|
| `minimal` (and `none`) | `min` |
| `low` | `standard` |
| `medium` | `extended` |
| `high` | `max` |

Raw web values (`min/standard/extended/max`) are also accepted verbatim, so a
client can target the web ladder directly through `reasoning_effort`.

## Implementation

- **`server.py`** — `_norm_effort(body)` reads `reasoning_effort` (or
  `reasoning.effort`), lower-cases it, and maps via `_EFFORT_MAP` to a web value
  or `None`. Passed to `session.submit(text, model, new_conv, effort)`; logged as
  `effort=`.
- **`browser.py`** —
  - `_load_effort_support()` runs at boot (reuses `_MODELS_JS`) and builds
    `{slug -> {allowed efforts}}` for models with `configurable_thinking_effort`.
    Empty on unsupported accounts.
  - `Job`/`submit()` carry `effort`; `_do_send` sets `self._forced_effort`.
  - `_apply_overrides(body, forced_model, forced_effort, effort_support)` (pure,
    unit-tested) is called from the `Fetch.requestPaused` handler where the model
    was already being rewritten. It injects `body["thinking_effort"]` **only when
    the effective model advertises support and the value is in its allowed set** —
    otherwise the field is omitted (sending an unsupported `thinking_effort` would
    risk a 400). Safe no-op on this account (empty support map).

## Validation

- **Unit** (`_norm_effort`, `_apply_overrides`, `_load_effort_support` parsing
  against enterprise- and this-account-shaped model JSON): all branches pass —
  including gate-out when the model is unsupported or the value isn't allowed.
- **Live** (this account): boot shows `effort_support == {}`; a real send forcing
  `model=gpt-5-mini` + `reasoning_effort=high` streamed a clean reply (`pong`)
  with the model override applied and `thinking_effort` correctly suppressed
  (an unsupported injection would have errored). The CDP postData
  rewrite/encode/continue path works with `_forced_effort` set.

**Not yet verified against a supporting login:** that the backend *accepts* an
injected `thinking_effort` and that it actually changes depth. That needs a run
against the enterprise account (or the offered discovery test: temporarily seed
`_effort_support` for a reasoning model here and see whether the backend accepts
the field even though the picker marks it non-configurable).
