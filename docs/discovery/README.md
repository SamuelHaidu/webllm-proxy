# Discovery notes index

This directory tracks reverse-engineering discoveries about the **login-only
web LLM backends** `webllm-proxy` bridges: ChatGPT's web backend (chatgpt.com)
and Databricks' Genie/`llmproxy` channel. See `CLAUDE.md` at the repo root for
why this exists and the discovery workflow.

Each file below is a dated log entry. Read newest-first for current
findings; older entries may be superseded (note if so). Entries below are
tagged **[chatgpt]** or **[databricks]**.

## Entries

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
