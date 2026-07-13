# Discovery notes index

This directory tracks reverse-engineering discoveries about the **login-only
web LLM backends** `webllm-proxy` bridges: ChatGPT's web backend (chatgpt.com)
and Databricks' Genie/`llmproxy` channel. See `CLAUDE.md` at the repo root for
why this exists and the discovery workflow.

Each file below is a dated log entry. Read newest-first for current
findings; older entries may be superseded (note if so). Entries below are
tagged **[chatgpt]**, **[databricks]**, **[copilot]**, or **[tokens]** (cross-
cutting utils work, not a backend-specific reverse-engineering finding).

## Entries

- **[chatgpt][databricks][copilot]** `2026-07-13-system-prompt-architecture.md` —
  survey of how the final system prompt is assembled, one per provider (not
  unified). chatgpt/copilot have no native system role/tool-calling, so both
  collapse the client's `messages` into text and prepend a synthesized system
  block via the shared `utils/tags.build_preamble()` (`utils/prompts.py`
  reads the framing `.md` files) -- chatgpt uses the strict "exactly these N
  tools and no others" contract plus a `_Planner` continuation heuristic that
  skips re-sending it on a continuing stateful thread; copilot uses a milder
  non-exclusive contract every turn (no continuity API), which live-testing
  separately confirmed Copilot's model still refuses to use at all. databricks
  talks a real Anthropic/Azure API instead (no tag emulation) but is
  **asymmetric between its own two channels**: the Claude channel always
  prepends a Genie/tone/safety system block (`llmproxy._prepend_system`,
  documented as necessary to defeat a server-side scope guard) + optional
  style rules, while the Azure/GPT channel injects nothing and forwards the
  client's system messages untouched. Also flags two prompt-store files
  (`databricks_genie_code_local_system_prompt.md`, `genie_code_tools.json`)
  that exist on disk but are wired into zero code paths.
- **[tokens]** `2026-07-13-token-usage-estimation.md` — real `usage.prompt_tokens`/
  `completion_tokens` (was a zeros placeholder). Ported `coder/ai-tokenizer`
  (MIT): real BPE counts via `tiktoken` (its 3 built-in vocabs, plus a
  vendored `claude` vocab table tiktoken doesn't ship) + a small per-model
  chat-format overhead model on top, adapted from Zod- to JSON-Schema tool
  walking. No per-model-slug mapping ported (wire ids don't match upstream's
  naming) -- instead, which vendored profile applies is **YAML-configurable at
  two levels**: a provider-level default (`providers.<name>.tokenizer`) and a
  per-model override (`providers.<name>.models.<slug>.tokenizer`, for when one
  provider mixes model families, e.g. databricks serving both Claude and GPT),
  both validated at config load. Defaults to `openai/gpt-5` for every provider
  until the operator says otherwise. Wired into all three providers'
  non-streaming completion paths via a new `wire.attach_usage()` fallback
  (prefers real upstream `usage` when a provider actually reports one).
  **Update (live testing):** found and fixed two real bugs in databricks'
  Claude channel that made it return a completely empty reply for any
  non-streaming request -- `AnthropicSSE` never captured Anthropic's own
  `message_start`/`message_delta` usage events despite this being the
  documented intended mapping since 2026-07-10, and `openai_to_anthropic()`
  forwarded the client's `stream` field upstream instead of always requesting
  SSE (a non-streaming request got a plain `application/json` body the SSE
  parser silently produced nothing from). Both channels now return 100% real
  `usage` in the common case; see the file for the full trace.
- **[copilot]** `2026-07-13-copilot-live-test.md` — added a tiny opt-in live smoke
  for the Copilot **deep-thinking** model (`tests/smoke_copilot_reasoning.py`, 2
  turns max, gated by `WEBLLM_PROXY_COPILOT_LIVE=1` to dodge the request-throttle).
  Found (and fixed) **two** login-detection bugs in a row: `authed()` was first
  hostname-only (false-positived the signed-out splash), then a title-based fix
  was *also* wrong (`m365.cloud.microsoft/`'s title stays "... - Sign in" even
  when logged in, so the login poll could never succeed there). **Real fix**:
  drive/detect via `m365.cloud.microsoft/chat` — a logged-out session can't reach
  it (bounces to `login.*`), a logged-in one always lands there; fallback to a
  live composer-presence check off that path. Added `login_steer()` to nudge a
  post-login browser back to the app if it settles on an off-app page
  (`office.com`). **Live-verified end to end**: `authed()` now correctly reports
  the logged-in session, and the gated smoke's both turns (non-stream + stream to
  `copilot__Reasoning`) passed with a correct real answer. `tests/test_copilot_auth.py`
  covers the new signal + `login_steer`. **Then**: replaced the static `_TONES`
  model list with **live discovery** from `POST /chat {action:RefreshNavPane}`'s
  `modelSelectorMetadata` (`providers/copilot/models.py`, no static list, no
  family/name mapping — matches the Databricks resolution's principle);
  live-verified all 5 real ids come back. Added a chat + emulated-tool-calling
  smoke (`tests/smoke_copilot_chat_tools.py`): **plain chat works live**;
  **emulated `<tool>` tag calling does not** — confirmed across 4 live variants
  (2 tools, default/softened/strengthened contract prompts) that M365 Copilot's
  own alignment refuses externally-declared tool schemas outright, real
  alternative or not, a genuine model constraint (not a prompt bug) — shipped a
  milder contract path anyway (`tags.build_preamble`'s new `contract_prompt`/
  `exclusive` params, chatgpt unaffected) and marked the test `xfail(strict=False)`
  so it's honest now and would loudly flip to `XPASS` if that ever changes.
  `poe check` green (59 tests).
- **[databricks]** `2026-07-13-databricks-model-discovery.md` — **Databricks model
  listing is now fully automatic** (resolves task 1 below). Pinned the
  `graphql/ConversationModelStatuses` query and found the operation is
  **server-safelisted**: `x-databricks-operation-identifier` is a persisted-op
  signature over operationName+query+**variables** (not a recomputable hash), so
  the request must be **replayed verbatim** (a trimmed single-clientId or
  reformatted query 400s "Graphql operation not authentic"). Pinned it as
  `providers/databricks/model_discovery.json`; `models()` replays it, filters
  in-page to `editor-assistant-agent-mode`, and lists **every** AVAILABLE name
  verbatim as `databricks__<name>` (no family mapping/flags/filtering; channel
  routing `gpt*`→Azure / else→Anthropic is completions-only). **Removed the
  static `models`/`openai_models` config + code.** Live probe
  (`scripts/dbx_models_probe.py`, new `discover` mode) → **10 usable chat models**
  (gpt-4o ×4, gpt-4.1 ×3, gpt-5-mini/nano, claude-4-5-sonnet); `*-combined` is
  entitled-but-500, embeddings/ghosttext skipped; gpt-5 rejects a non-default
  `temperature`. Also refreshed the captured Genie Code system prompt (37 KB).
- **[databricks][copilot]** `2026-07-13-open-model-discovery-tasks.md` — after the
  architecture rebuild, the two remaining live-session tasks to make automatic
  model listing fully hands-off: pin the Databricks `ConversationModelStatuses`
  GraphQL query/response (and whether it carries the Azure `gpt-*` deployments),
  and capture the Copilot M365 `RefreshNavPane` capability manifest.
- **[chatgpt]** `2026-07-12-emulated-thinking.md` — **Emulated "thinking mode"**
  for the `webllm-agent` emulated agent: since chatgpt.com reasons poorly inline,
  each pi turn runs **two independent fresh chats** — a reasoning chat that
  returns a `<thinking>` block (self-questioning, competing hypotheses, edge-case
  enumeration, a "Wait — is that verified?" recheck) surfaced as a **native pi
  reasoning block**, then an action chat that receives the thinking and emits one
  command; a prose (non-action) reply loops back into the reasoning chat before
  being accepted. Key constraint driving the design: the proxy's single
  `ConversationPlanner` can't thread two interleaved conversations, so **each call
  is one self-contained user message**. On by default; toggle with `/webllm-agent
  thinking on|off` (`WEBLLM_CHATGPT_THINKING`). Code in `integrations/pi/src/agentThinking.ts`.
- **[databricks]** `2026-07-12-genie-code-agent.md` — **Reproducing the real
  Genie Code agent** (browser experience in the terminal via pi, tools executed
  **remotely**, pi a thin client). Reverse-engineered from the real Genie Code
  HAR: it's a **client-orchestrated loop** (`llmproxy` is model-only; the browser
  executes each `tool_use` against a per-tool Databricks endpoint and feeds the
  `tool_result` back), so the design is **proxy-as-orchestrator**. Captured the
  real **37 KB system prompt** (stored sanitized at
  `webllm_proxy/prompts/genie_code_system.md`) and the full **30-tool inventory**;
  stored the **read-only subset** schemas (`docSearch`, `tableSearch`,
  `querySearch`, `readTable`, `searchAssets`, `askGenieSpace`, `manageTodoList`)
  at `genie_code_tools.json`. Only `docSearch` (→ `POST /graphql/DocsSearch__deduped`)
  was exercised in the HAR, so it's the one fully spec'd executor (the template);
  the other five need a fresh capture that triggers them. `executeCode` (notebook/
  cluster) explicitly deferred.
- **[ms365]** `2026-07-11-ms365-copilot-sydney.md` — **NEW TARGET candidate:
  Microsoft 365 Copilot (BizChat consumer)**, third potential backend.
  Pre-browser HAR scoping. The chat turn is a **SignalR ChatHub over WebSocket**
  to `substrate.office.com/m365Copilot/Chathub/{conversationId}` (the "Sydney"
  backend), authorized by an **OAuth Bearer** token (scope
  `substrate.office.com/sydney/v2/.default`, MSAL, consumer tenant
  `9188040d-...`), **no Turnstile/PoW**. Trouter (`*.trouter.skype.com`,
  `appId: bizchat`) is the async notify channel. The HAR has **no** chat
  completion body (answer text absent everywhere; no `wss://`/101 recorded) —
  proof the stream is WS frames, which the current HTTP-only CDP transport
  doesn't capture. Capability manifest gives the model selector
  (`Magic`/`Chat`/`Reasoning`/`Gpt_5_5_*` = Auto/Quick/Think Deeper/GPT-5.5) and
  server-side tool toggles (`executionControls`: connectors/work/web/
  personalOneDrive/builtInPlugins/localDevice) — **extensibility disabled**, so
  it maps to a **chat model, not a native `tool_use` agent backend** like
  Databricks. Verdict: bridgeable but hardest of the three; blocker is
  engineering (add WebSocket-frame capture + reverse the ChatHub SignalR schema
  live), not anti-bot. **Update (same day): full ChatHub WebSocket protocol
  captured** from a second HAR (WS frames under `_webSocketMessages`) — it's the
  standard **Sydney/BingChat SignalR** protocol: `type:4 target:"chat"`
  invocation (`message.text`, `tone`=model, `plugins`+`optionsSets`=tools),
  cumulative `update` text deltas, final `StreamItem`+`Completion` (carries the
  full answer). Added `ws`/`wsshow` subcommands to `scripts/har_explore.py`
  (SignalR-aware, redaction of `access_token`/`signature`). Only token+
  conversationId acquisition and off-browser replay-binding remain to confirm
  live. **Update 2: captured the consumer `copilot.microsoft.com` variant** (a
  third edition, newer **event-JSON** protocol on `/c/api/chat`: `send`/
  `appendText`/`done`, in-band **hashcash** PoW + Cloudflare Turnstile) and
  **shipped two deliverables** — `docs/protocol/copilot-protocol.md` (full spec
  for both wire protocols + all three editions) and **`unicopilot/`**, a
  detachable universal client (edition-agnostic core; `SignalRCodec`/`EventCodec`
  validated to decode the real captured frames exactly — M365 993-char and
  consumer 1587-char answers reconstructed).
- **[chatgpt]** `2026-07-11-deep-research-scoping.md` — **Scoping note, not a
  trigger.** Explains why the research-job feature's Deep Research backend
  ships as a documented stub (`available()` hardcoded `False`) rather than a
  live-discovered trigger: the account is confirmed free-tier (Update 5
  below) and Deep Research is historically paid-tier-gated, so a live
  discovery session would likely find nothing to toggle. The emulated backend
  (a plain chat turn + a research-style prompt, no trigger needed) ships
  instead and is **live-verified working** (real web search, real cited
  sources, structured markdown report, ~10s). Lays out the concrete
  capture-and-diff steps for whenever an entitled account is available.
- **[databricks]** `2026-07-10-databricks-llmproxy.md` — **NEW TARGET:
  Databricks Genie/assistant `llmproxy`** (separate from ChatGPT). Pre-browser HAR analysis via
  `scripts/har_explore.py`. The Genie-code channel `POST
  /ajax-api/2.0/conversation/llmproxy/` is a thin passthrough to the **native
  Anthropic Messages API on AWS Bedrock** (Claude Sonnet 4.5, native `tool_use`,
  extended `thinking`), model chosen by `_llmproxy_fields.model_registration`.
  A sibling `proxy/chat/completions` is Azure-OpenAI-shaped. **Auth = session
  cookie + `x-csrf-token` (served by `/auth/session/info`) + org-id; NO
  Turnstile/PoW** — so a mostly server-side proxy is feasible and tool-calling
  is native (no emulation). Lists the open questions for the browser phase.
  **Update: made a real coding agent (`pi`) work end-to-end** — two fixes: (1)
  strip `eager_input_streaming` from tools (a pi-added field the llmproxy→Bedrock
  passthrough 400s on), (2) prepend a **Genie-agent system framing** to defeat the
  `editor-assistant-agent-mode` channel's out-of-context scope guard (pi's system
  prompt, with local `/home/...` paths, otherwise triggers "scoped to Databricks"
  refusals). Result: native `tool_use` loop builds the calc + 19 passing tests;
  Databricks is the more reliable tool-calling backend vs ChatGPT's emulation.
  Also: **Anthropic `count_tokens` is NOT supported by the llmproxy channel**
  (edge 400; only `anthropic/v1/messages` is whitelisted; Genie never calls it;
  `pi` doesn't need it — uses response `usage`). Added a `count_tokens` route
  that tries the backend then falls back to a local ~4-chars/token estimate.
  **Model-discovery runbook + `scripts/dbx_models_probe.py`** (trial + error-
  taxonomy over both channels via direct in-page fetch): usable set on this
  account = **Claude Sonnet 4.5** (`anthropic` channel) **+ `gpt-41-2025-04-14`
  / `gpt-41-mini-2025-04-14`** (`azure` `proxy/chat/completions` channel,
  streaming confirmed); all other names are `DISABLED` (gated) or `NOT_FOUND`.
  **GPT-4.1 Azure channel now wired** as OpenAI `POST /v1/chat/completions`
  (two-channel transport: the job payload carries the target sub-path). Stream
  passthrough + non-stream assembly + native `tool_calls`; validated with `pi`
  end-to-end. Databricks now serves Claude (Anthropic) **and** GPT-4.1 (OpenAI).
  **Update (2026-07-12): `genie_framing.md` expanded into a full Claude-Code/
  Codex-inspired agentic prompt** (tone, conventions, verification, tool-usage
  policy, safety) while keeping the proven scope-guard-defeating identity
  paragraph verbatim at the top; still layers under the caller's own system.
- **[chatgpt]** `2026-07-10-thinking-effort.md` — **Reasoning effort.** `/backend-api/models`
  advertises per-model `configurable_thinking_effort` + `thinking_efforts`, and
  the `f/conversation` body carries a root `thinking_effort` on a 4-level ladder
  `min<standard<extended<max`. Mapped 1:1 from OpenAI `reasoning_effort`
  (minimal/low/medium/high) and injected into the request (gated to models that
  support it, so it's a no-op on accounts that don't). Also noted:
  `versions[].intelligence_presets`.
- **[chatgpt]** `2026-07-10-tool-calling.md` — **Tool calling, both kinds**
  (see Update 3 for the native-channel interception that superseded the
  text-only contract, **Update 4** for the AgentClip tag-contract port + its
  live `pi` re-validation, and **Update 5** for hijacking the thinking model's
  `container.exec` sandbox → real `bash` + capturing its reasoning/thinking
  tokens for pi). (1) ChatGPT's
  **native tools** (web search): routing is by `author.role`+`recipient`+
  `channel` (not content_type), and the answer is polluted with private-use-area
  citation markers (`…`) that we strip / render as markdown links.
  (2) **OpenAI-style function calling**, which the web backend can't do natively,
  is **emulated** via a tag prompt contract (`<assistant>`/`<tool>`/
  `<tool-response>`, ported from AgentClip's `system_prompt.md`) + parser;
  stateful planner generalized to signature-diffing so `role:"tool"` results
  feed back. Validated with a direct client and the `pi` agent (recursive-
  descent calculator build, `gpt-5-mini`: reliable in 2/2 runs after fixing an
  unclosed-`<assistant>`-tag leak; `gpt-5-4-t-mini`: still hallucinates via
  ChatGPT's native sandbox, unaffected by the contract — an open,
  architectural problem, not a wording one; **`auto`/`gpt-5-5`: refuse the
  contract outright, 4/4 runs** — they correctly identify the injected
  "SYSTEM INSTRUCTIONS" block as user text and decline to treat it as
  authoritative, so `gpt-5-mini` is the only reliable tool-calling model for
  now). Contract mandates one call per reply (fixes a parallel
  read-before-write race).
- **[chatgpt]** `2026-07-10-refactor-packaging.md` — Consolidated into a lean
  full-Python `uv` package (`src/chatgpt_proxy/`, dist `chatgpt-web-proxy`);
  removed the Go port, old DOM-scraping scripts, and the frontend; moved the
  login profile to `~/.local/share/chatgpt-proxy/`; **fixed the browser
  lifecycle so SIGTERM leaves 0 orphan Chrome** (boot clears stale locks,
  shutdown kills profile-scoped Chrome). **Superseded**: the package was
  renamed again to `src/webllm_proxy/` / `webllm-proxy` in the 2026-07-10
  provider-adapter unification (see `CLAUDE.md`); profile paths/env names
  didn't change (back-compat).
- **[chatgpt]** `2026-07-10-v1-proxy.md` — **v1 proxy built (`openai_proxy/`) +
  basics verified**: streaming, model selection, thinking models, stateful
  recall. Mechanism findings (window.fetch hook fails → CDP
  `streamResourceContent` streaming + `Fetch` model override) and the `v1`
  parser bugs fixed ((o,p) inheritance, assistant-role gating).
- **[chatgpt]** `2026-07-10-browser-backed-validation.md` — Validated the
  chosen architecture end-to-end: **headless** send on the persisted login
  works, **CDP SSE capture** is reliable (body-eviction fix), and a
  **stateful follow-up retained context** ("Zephyrine"/27 recalled).
  Documents the reusable UI-trigger + CDP-capture primitive the proxy is
  built on.
- **[chatgpt]** `2026-07-10-backend-api-capture.md` — **The web backend API,
  captured authenticated.** Send = `POST /backend-api/f/conversation` (SSE,
  `v1` delta encoding), gated by a **sentinel** flow needing 3 per-request
  tokens (chat-requirements + proof-of-work + **Cloudflare Turnstile**) plus a
  Bearer JWT from `/api/auth/session`. Full request/response schema, OpenAI
  mapping, and the resulting architecture decision (browser-backed vs
  pure-HTTP). Sanitized samples in `samples/`.
- **[chatgpt]** `2026-07-10-cloakbrowser.md` — **VALID FRAMEWORK.**
  CloakBrowser (stealth Chromium, Playwright drop-in) **passes Cloudflare
  Turnstile on chatgpt.com in headless mode** — the thing Flatpak Chrome
  headless could not do. Adopted as the browser layer for both providers
  (auto-downloads its own binary, runs natively here, persistent-context for
  cookies). Reproducible setup + runtime facts.
- **[chatgpt]** `2026-07-10-setup-and-cloudflare.md` — Verified:
  chrome-devtools-mcp + Flatpak Chrome launch recipe; **headless is blocked by
  Cloudflare Turnstile, must run headed**; fresh profiles have no cookies;
  Chrome 136+ refuses remote debugging on the real/default profile dir; the
  two clean paths to a persistent logged-in profile (login-once vs
  copy-cookies-once). Network capture still pending.
