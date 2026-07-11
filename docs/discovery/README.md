# Discovery notes index

This directory tracks reverse-engineering discoveries about the **login-only
web LLM backends** `webllm-proxy` bridges: ChatGPT's web backend (chatgpt.com)
and Databricks' Genie/`llmproxy` channel. See `CLAUDE.md` at the repo root for
why this exists and the discovery workflow.

Each file below is a dated log entry. Read newest-first for current
findings; older entries may be superseded (note if so). Entries below are
tagged **[chatgpt]** or **[databricks]**.

## Entries

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
- **[chatgpt]** `2026-07-10-thinking-effort.md` — **Reasoning effort.** `/backend-api/models`
  advertises per-model `configurable_thinking_effort` + `thinking_efforts`, and
  the `f/conversation` body carries a root `thinking_effort` on a 4-level ladder
  `min<standard<extended<max`. Mapped 1:1 from OpenAI `reasoning_effort`
  (minimal/low/medium/high) and injected into the request (gated to models that
  support it, so it's a no-op on accounts that don't). Also noted:
  `versions[].intelligence_presets`.
- **[chatgpt]** `2026-07-10-tool-calling.md` — **Tool calling, both kinds**
  (see Update 3 in the file for the native-channel interception that
  superseded the text-only contract). (1) ChatGPT's
  **native tools** (web search): routing is by `author.role`+`recipient`+
  `channel` (not content_type), and the answer is polluted with private-use-area
  citation markers (`…`) that we strip / render as markdown links.
  (2) **OpenAI-style function calling**, which the web backend can't do natively,
  is **emulated** via a `tool_call` prompt contract + parser; stateful planner
  generalized to signature-diffing so `role:"tool"` results feed back. Validated
  with a direct client and the `pi` agent; contract mandates one call per reply
  (fixes a parallel read-before-write race).
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
