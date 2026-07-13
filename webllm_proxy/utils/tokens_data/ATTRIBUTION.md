# Vendored from `coder/ai-tokenizer`

Source: <https://github.com/coder/ai-tokenizer> (MIT, Copyright (c) 2025 Coder
Technologies Inc.), commit as of 2026-07-13. Pulled in per
`webllm_proxy/utils/tokens.py`'s port.

- **`claude_encoding.json`** — verbatim copy of `src/encoding/claude.json`. A
  BPE vocab (`bpe_ranks`, tiktoken `.tiktoken`-file format: `<hash> <offset>
  <base64-token>...` per line), regex split pattern (`pat_str`), and special
  tokens for Anthropic's last *publicly documented* tokenizer. Claude 3+
  models don't publish a real tokenizer, so this is every Claude model's
  best available approximation (~97-99% accuracy per upstream's own
  measurements against live API `usage`) -- same approximation upstream
  ships, not weakened further by this port.
- **`model_profiles.json`** — trimmed copy of `src/models.json` (100 models):
  kept only `encoding` (which of `o200k_base`/`cl100k_base`/`p50k_base`/
  `claude` the model's BPE vocab is) and `tokens` (small per-model overhead
  constants: `baseOverhead`, `perMessage`, `perTool`, `perDesc`,
  `perFirstProp`, `perAdditionalProp`, `perPropDesc`, `perEnum`,
  `perNestedObject`, `perArrayOfObjects`, `contentMultiplier`, `toolsExist`).
  Dropped `name`/`contextWindow`/`maxTokens`/`pricing` -- irrelevant to token
  counting, not tracked here.

Not vendored: `o200k_base`/`cl100k_base`/`p50k_base` themselves (OpenAI's
public tiktoken encodings) -- `tiktoken` (a runtime dependency here) already
ships all three natively; no need to duplicate multi-MB vocab files upstream
regenerates from the same public source.

## The method (for anyone re-deriving this later)

1. **Real BPE token counts**, not word/char heuristics. Every model picks one
   of the 4 vocabs above; `tokenizer.py`'s `Tokenizer` class builds a
   `tiktoken.Encoding` per vocab and calls `.encode(text)`.
2. **A small additive overhead model on top of raw BPE counts** for the
   chat-format wrapping tokens/tool-schema serialization that a raw text
   encode alone can't see (mirrors OpenAI's own documented
   `<|start|>{role}\n{content}<|end|>` per-message overhead, generalized
   per-provider): `usage.py`'s `estimate_prompt_tokens()` ports upstream's
   `src/sdk.ts` `count()` -- per-message role + content encode plus
   `perMessage`, per-tool name/description/JSON-Schema-property encode plus
   `perTool`/`perDesc`/`perFirstProp`/etc., all fitted per-model by upstream
   against real API responses (see their `accuracy.json`).
3. **No model-id name-mapping table is ported.** Upstream's 100 profiles use
   their own `<provider>/<slug>` naming (`anthropic/claude-sonnet-4.5`, ...),
   which doesn't line up with this project's wire ids (`databricks__claude-4-5-sonnet`,
   `copilot__Gpt_5_5_Chat`, ...). Rather than a fragile per-slug matching
   table, which profile applies is **YAML-configurable at two levels** in
   `webllm-proxy.yaml` (both validated against `tokens.available_profiles()`
   at config load by `utils.config`, applied via `tokens.configure_profiles()`/
   `configure_model_profiles()` once at server boot):
   - `providers.<name>.tokenizer` -- the provider's default.
   - `providers.<name>.models.<slug>.tokenizer` -- an override for one
     specific model (wins over the provider default for that model), for
     providers whose model list mixes families (e.g. a databricks workspace
     serving both Claude and GPT models).

   Default is `openai/gpt-5` for every provider/model with no override -- no
   per-provider or per-model guessing baked into the code. This is an
   explicit, documented approximation on top of an already-approximate
   upstream library -- good enough for a `usage` estimate, not a source of
   truth.
