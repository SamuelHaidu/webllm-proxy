# Discovery notes index

This directory tracks reverse-engineering discoveries about ChatGPT's web
backend API (chatgpt.com), for the purpose of building a local proxy that
speaks OpenAI-API-compatible schema. See `CLAUDE.md` at the repo root for
why this exists and the discovery workflow.

Each file below is a dated log entry. Read newest-first for current
findings; older entries may be superseded (note if so).

## Entries

- `2026-07-10-tool-calling.md` — **Tool calling, both kinds.** (1) ChatGPT's
  **native tools** (web search): routing is by `author.role`+`recipient`+
  `channel` (not content_type), and the answer is polluted with private-use-area
  citation markers (`…`) that we strip / render as markdown links.
  (2) **OpenAI-style function calling**, which the web backend can't do natively,
  is **emulated** via a `tool_call` prompt contract + parser; stateful planner
  generalized to signature-diffing so `role:"tool"` results feed back. Validated
  with a direct client and the `pi` agent; contract mandates one call per reply
  (fixes a parallel read-before-write race).
- `2026-07-10-refactor-packaging.md` — Consolidated into a lean full-Python
  `uv` package (`src/chatgpt_proxy/`, dist `chatgpt-web-proxy`); removed the
  Go port, old DOM-scraping scripts, and the frontend; moved the login profile
  to `~/.local/share/chatgpt-proxy/`; **fixed the browser lifecycle so SIGTERM
  leaves 0 orphan Chrome** (boot clears stale locks, shutdown kills
  profile-scoped Chrome).
- `2026-07-10-v1-proxy.md` — **v1 proxy built (`openai_proxy/`) + basics
  verified**: streaming, model selection, thinking models, stateful recall.
  Mechanism findings (window.fetch hook fails → CDP `streamResourceContent`
  streaming + `Fetch` model override) and the `v1` parser bugs fixed
  ((o,p) inheritance, assistant-role gating).
- `2026-07-10-browser-backed-validation.md` — Validated the chosen
  architecture end-to-end: **headless** send on the persisted login works,
  **CDP SSE capture** is reliable (body-eviction fix), and a **stateful
  follow-up retained context** ("Zephyrine"/27 recalled). Documents the
  reusable UI-trigger + CDP-capture primitive the proxy is built on.
- `2026-07-10-backend-api-capture.md` — **The web backend API, captured
  authenticated.** Send = `POST /backend-api/f/conversation` (SSE, `v1` delta
  encoding), gated by a **sentinel** flow needing 3 per-request tokens
  (chat-requirements + proof-of-work + **Cloudflare Turnstile**) plus a Bearer
  JWT from `/api/auth/session`. Full request/response schema, OpenAI mapping,
  and the resulting architecture decision (browser-backed vs pure-HTTP).
  Sanitized samples in `samples/`.
- `2026-07-10-cloakbrowser.md` — **VALID FRAMEWORK.** CloakBrowser (stealth
  Chromium, Playwright drop-in) **passes Cloudflare Turnstile on chatgpt.com
  in headless mode** — the thing Flatpak Chrome headless could not do.
  Adopted as the browser layer (auto-downloads its own binary, runs natively
  here, persistent-context for cookies). Reproducible setup + runtime facts.
- `2026-07-10-setup-and-cloudflare.md` — Verified: chrome-devtools-mcp +
  Flatpak Chrome launch recipe; **headless is blocked by Cloudflare
  Turnstile, must run headed**; fresh profiles have no cookies; Chrome 136+
  refuses remote debugging on the real/default profile dir; the two clean
  paths to a persistent logged-in profile (login-once vs copy-cookies-once).
  Network capture still pending.
