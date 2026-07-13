# Token usage estimation (`usage.prompt_tokens`/`completion_tokens`)

Not a backend-reverse-engineering entry like the rest of this directory --
recorded here anyway per the discovery workflow, since it answers "how do we
get real-looking numbers into the `usage` field" and is the kind of thing a
future session would otherwise re-investigate from scratch.

## The problem

`utils/tokens.py`'s `usage()` was a zeros placeholder since the rebuild --
every non-streaming completion returned `{"prompt_tokens": 0,
"completion_tokens": 0, "total_tokens": 0}` regardless of what actually went
through. None of the three providers have a real token-count API of their own
(chatgpt/copilot: no such endpoint at all; databricks: only the Azure GPT
channel occasionally echoes a real `usage` in its SSE, and even that isn't
guaranteed).

## What was ported

[`coder/ai-tokenizer`](https://github.com/coder/ai-tokenizer) (MIT, npm
`ai-tokenizer`) -- picked because the placeholder comment already named it as
the intended target. Cloned it to `/tmp` to read the actual source (not just
the README) before deciding what to vendor. Two things worth separating:

1. **The counting method is real BPE, not a word/char heuristic.** Every
   model in the library picks one of 4 BPE vocabs
   (`o200k_base`/`cl100k_base`/`p50k_base`/`claude`) and does a real
   byte-pair-encode over the text. `src/tokenizer.ts` is a from-scratch,
   heavily hand-optimized BPE engine -- but it's the *same* algorithm
   `tiktoken` already implements, and `tiktoken` (added as a runtime dep
   here) ships `o200k_base`/`cl100k_base`/`p50k_base` natively. So none of
   `tokenizer.ts` needed porting -- just call `tiktoken.get_encoding(name)`.
   The one vocab `tiktoken` doesn't have is `claude` (Anthropic's last
   *publicly documented* tokenizer; Claude 3+ ships no public one, so this is
   every Claude model's best available approximation, same one upstream
   uses). That table (`src/encoding/claude.json` -- BPE ranks in the same
   `<hash> <offset> <base64-token>...`-per-line format as tiktoken's own
   `.tiktoken` files, plus a split pattern and special tokens) was vendored
   verbatim as `utils/tokens_data/claude_encoding.json`, and `tiktoken.Encoding`
   (which accepts *any* custom vocab/pattern/special-tokens, not just its
   4 built-ins) builds a working "claude" encoding from it directly -- no
   hand-rolled merge algorithm needed in Python either.
2. **A small additive overhead model on top of raw BPE counts**, fitted
   per-model against real API responses (`src/sdk.ts`'s `count()`, upstream's
   own `accuracy.json` shows ~95-99% depending on model): per-message
   role/content overhead, per-tool name/description/JSON-Schema-property
   overhead, an enum/nested-object/array-of-objects surcharge, a content
   multiplier. This *is* genuinely per-model logic, not a table --
   ported to `utils/tokens.py`'s `_message_tokens`/`_tools_tokens`/
   `_schema_tokens`, adapted from Zod-schema walking (upstream's AI-SDK
   input shape) to plain JSON-Schema dict walking (this project's OpenAI
   tool-def shape). The 100 models' fitted constants + which vocab each uses
   were vendored as `utils/tokens_data/model_profiles.json` (trimmed: dropped
   `pricing`/`contextWindow`/`maxTokens`/`name`, kept only `encoding` +
   `tokens`).

Full attribution/provenance/license notes live next to the vendored data:
`utils/tokens_data/ATTRIBUTION.md`.

## What wasn't ported: per-model-slug mapping -- made configurable instead

Upstream's 100 profiles use their own `<provider>/<slug>` naming
(`anthropic/claude-sonnet-4.5`, `openai/gpt-5`, ...), which doesn't line up
with this project's wire ids (`databricks__claude-4-5-sonnet`,
`copilot__Gpt_5_5_Chat`, `chatgpt__gpt-5-thinking`). A fragile per-slug
matching table felt like exactly the kind of undocumented heuristic this repo
has avoided elsewhere (model *discovery* is explicitly "no mapping, no name
heuristics" per `databricks`/`copilot`'s own docs). Rather than guess a
per-provider default in code (an earlier version of this picked
`anthropic/claude-sonnet-4.5` for databricks since Genie routes to Claude),
it's now **user-configurable YAML fields at two levels** instead, since one
provider can serve models from different families (databricks in particular
mixes Claude and GPT models behind one workspace):

- `providers.<name>.tokenizer` -- the provider's default (one of
  `tokens.available_profiles()`, validated at config load).
- `providers.<name>.models.<slug>.tokenizer` -- an override for one specific
  model (keyed by the slug after `<provider>__` in its wire id), winning over
  the provider default for just that model.

Default is `openai/gpt-5` for every provider with no overrides, unconditionally,
until the operator says otherwise -- no guessing baked in, just an honest
default plus an escape hatch for whoever actually knows what's behind each
model. Wired via `utils.config.Config.tokenizer_profiles()`/
`model_tokenizer_overrides()` -> `tokens.configure_profiles()`/
`configure_model_profiles()`, both called once in `server.serve()` at boot.
This is an explicit, documented approximation layered on an already-approximate
upstream library -- fine for a `usage` estimate, not something to treat as
ground truth.

## Wiring

`utils/openai.py`'s `completion()` gained an optional `usage_dict` param
(defaults to zeros, unchanged); a new `attach_usage(result, messages, tools,
model, real_usage=None)` helper sets `result["usage"]` to `real_usage` when a
provider actually got one from upstream, otherwise estimates it from the
original request plus the assembled reply (no-ops on an error dict, so
providers can call it unconditionally on their return value). All three
providers' non-streaming completion paths now call it; streaming responses
are left without a `usage` chunk, matching plain OpenAI behavior when the
client didn't ask for `stream_options.include_usage`.

## Update 2026-07-13 — live verification found & fixed a real bug: databricks'
## Claude channel wasn't returning native usage at all

Live-tested all three providers end to end (`webllm-proxy serve` +
`/v1/chat/completions`) to confirm this actually works and to configure
per-model `tokenizer:` overrides from the real `GET /v1/models` output.
chatgpt and copilot came back estimated as expected (~17-19 total tokens for
a one-line prompt). **databricks' Claude channel came back completely
empty** (`content: null`, `usage: {17, 0, 17}` -- suspiciously close to what
the *estimate* fallback alone would produce from just the client's short
message, not the real request that goes upstream with a huge injected system
prompt + ~30 tools).

Root-caused with `WEBLLM_PROXY_DUMP_SSE=<path>` (dumps every raw captured
network payload, see `gateways/cloakbrowser/session.py`) -- the dump file
was never even created, meaning **zero bytes were ever captured** for that
turn, yet the turn "succeeded" (no error). Traced to two independent bugs
found together:

1. **The intended `usage` mapping was never actually implemented.**
   `docs/discovery/2026-07-10-databricks-llmproxy.md` documented `usage` ->
   `message_start.usage` as the target mapping from day one, but
   `utils/convert.py`'s `AnthropicSSE._handle()` never had a case for
   `message_start` at all, and `message_delta` only read `.delta.stop_reason`,
   ignoring its own top-level `usage` field (Anthropic's streaming API reports
   input-side usage on `message_start.message.usage` and output-side usage on
   `message_delta.usage`, cache tokens included in the former). **Fixed**:
   both are now captured into `AnthropicSSE.usage` (merged as they arrive) and
   exposed via `.openai_usage()` (a new `anthropic_usage_to_openai()` sums
   `input_tokens + cache_creation_input_tokens + cache_read_input_tokens` for
   `prompt_tokens`, `output_tokens` for `completion_tokens`, `None` if nothing
   was ever reported). `databricks/__init__.py`'s `_claude_completions` now
   passes `parser.openai_usage()` as `attach_usage()`'s `real_usage`, so this
   channel prefers real numbers exactly like the Azure/GPT channel already
   did, only estimating as a fallback.
2. **This is what made bug 1 additionally return a totally empty reply, not
   just zeroed usage.** `openai_to_anthropic()` forwarded the OpenAI client's
   own `stream` field straight into the upstream Anthropic request body
   (`"stream": bool(req.get("stream", True))`). My test request used
   `"stream": false` (a plain non-streaming client call) -- which upstream
   honored, replying with a small buffered `application/json` body instead of
   `text/event-stream`. `AnthropicSSE._line()` only parses `data:`-prefixed
   SSE lines, so a plain JSON body silently produces zero events and no
   error -- CDP still reports `Network.loadingFinished`, so the turn "completes"
   with the *initial* defaults (`content=""`, `finish_reason="stop"`). **Fixed**:
   `openai_to_anthropic()` now hardcodes `"stream": True` unconditionally --
   this proxy's capture layer only ever understands SSE, and the client-facing
   stream/non-stream choice was always handled separately anyway
   (`_stream_claude` vs `_nonstream_claude` collapsing the SSE); this exactly
   mirrors the Azure/GPT channel's `build_azure_body`, which already
   hardcodes `stream: true` for the same reason. Re-tested live after the fix:
   real content + real usage (`prompt_tokens: 2223, completion_tokens: 110`
   for an 8-word prompt -- the 2223 reflecting the actual injected system
   prompt/tools, something the estimate fallback could never have seen since
   it only has the client's own `messages`/`tools`).

Also live-confirmed the Azure/GPT channel already returns full native usage
including `prompt_tokens_details`/`completion_tokens_details` -- no bug there,
`assemble_completion`'s existing `up_usage` preference was already correct.

**Net effect: `databricks` now returns 100% real usage on both channels** in
the common case; the `tokenizer`/`models` YAML config for it is a fallback
for the rare turn upstream doesn't report usage on, not the primary source
anymore (still worth setting, and set in `webllm-proxy.yaml` from the real
`GET /v1/models` model mix for that reason). chatgpt and copilot have no
native usage API at all, so their config is the primary source: `chatgpt`
keeps the flagship default plus per-model overrides for the `-mini`/`-t-mini`
web slugs (`openai/gpt-5-mini`, the closest vendored match -- these are
ChatGPT's internal web slugs, not official API model ids, so there's no exact
match for the flagship variants either); `copilot`'s models are all GPT-5.5
per their own on-screen labels, and there's no vendored `gpt-5.5` profile, so
they all stay on the flagship default (no per-model split needed there).
