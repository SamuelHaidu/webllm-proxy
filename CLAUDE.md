# Project Notes for Claude Code

## Project intent

Build a local HTTP API proxy that talks to ChatGPT's underlying web backend
(the network API the chat.openai.com / chatgpt.com frontend itself calls,
e.g. `backend-api/conversation` and friends) and translates it to an
OpenAI-API-compatible schema (`/v1/chat/completions` style). Goal: let
coding agents / tools built against the OpenAI API work for people in
enterprise environments who have a ChatGPT web login but no official API
key/budget.

The implementation lives in **`src/chatgpt_proxy/`** (a `uv` package,
`chatgpt-web-proxy`). Earlier prototypes that drove the web UI by DOM
scraping — `chatgpt_api_server.py`, `manual_login.py`, a `go-api/` Go/CDP
port, and a React `frontend/` — were **removed in the 2026-07-10 refactor**
(full-Python, lean, uv-packaged). The current approach captures the real
`backend-api/f/conversation` network stream over CDP rather than scraping the
DOM.

### Status & key findings (2026-07-10)

- **Proxy BUILT + verified**, now a `uv` package in **`src/chatgpt_proxy/`**.
  Run: `uv sync` then `uv run chatgpt-proxy` (subcommands: `serve` default,
  `login`, `install`). Port 5102. Working: `/v1/models` (real slugs),
  `/v1/chat/completions` stream + non-stream, model selection, thinking
  models, stateful recall. See `README.md` and `docs/discovery/`.
  Mechanism: type prompt in the browser → capture the `f/conversation` SSE via
  CDP `Network.streamResourceContent` (a `window.fetch` hook does NOT work) →
  parse `v1` deltas → OpenAI chunks; model override via CDP `Fetch`.
- **Tool/function calling DONE (2026-07-10)** — see
  `docs/discovery/2026-07-10-tool-calling.md`. ChatGPT web has no client-facing
  function-calling API, so it's **emulated** (`tools.py`): inject a `tool_call`
  prompt contract, parse the model's block back into OpenAI `tool_calls`
  (`finish_reason:"tool_calls"`), feed `role:"tool"` results back as the next
  turn. `plan_turn` now signature-diffs the whole `messages[]` (not just user
  turns) so tool flows continue on one ChatGPT conversation. Contract mandates
  **one tool call per reply** (proxy is serialized; fixes a parallel
  read-before-write race). Tool-enabled requests are buffered, not streamed.
  Also: **native web search** works and its private-use-area citation markers
  (`…`) are stripped/rendered as markdown (`sse.py::_declutter`);
  native-tool messages are gated out by `recipient != "all"`. Validated with a
  direct client and the `pi` agent.
- **Native-channel tool-call interception DONE (2026-07-10, best path)** — see
  `docs/discovery/2026-07-10-tool-calling.md` (Update 3). The models often ignore
  the text contract and call tools through **ChatGPT's own native channel**: an
  SSE `assistant` message with `recipient` = the tool name and the **args as
  valid JSON** in the body (`content.text` for `content_type:"code"`, or `parts`
  for `"text"` — non-deterministic; sometimes recipient is literally
  `tool_call`). `sse.py` now captures any non-`all` recipient text
  (`native_calls` → `("tool_call", …)` events); `tools.py::native_to_openai`
  filters to client-declared tools and converts to OpenAI `tool_calls`;
  `server.py` prefers the native call over the text fence. Validated by curl:
  `gpt-5-mini` → real `write`/`bash` tool_calls 5/6. **Model-dependent:**
  `gpt-5-5-mini` fabricates "ALL TESTS PASSED" without calling tools; the thinking
  model runs in ChatGPT's sandbox. Debug: `CHATGPT_PROXY_DUMP_SSE=<file>` dumps
  raw SSE. **Next: still flaky end-to-end for multi-step builds; consider the
  request-body native-tool disable, and a per-model tool-reliability note.**
- **Reasoning effort DONE (2026-07-10)** — see
  `docs/discovery/2026-07-10-thinking-effort.md`. `/backend-api/models` carries
  per-model `configurable_thinking_effort` + `thinking_efforts`; the
  `f/conversation` body has a **root `thinking_effort`** on a 4-level ladder
  `min<standard<extended<max`. OpenAI `reasoning_effort`
  (`minimal|low|medium|high`, or `reasoning.effort`) maps 1:1 → injected into the
  request via `_apply_overrides` in the `Fetch.requestPaused` handler, **gated**
  to models advertising support (boot caches `_effort_support` from the models
  list). Safe no-op on the dev account (nothing configurable there). Not yet
  verified against a supporting (enterprise) login. Also seen:
  `versions[].intelligence_presets` in the models list (unused so far).
- **Note (env):** the user's shell sets `UV_ENV_FILE=.env`, so `uv run` needs
  a `.env` in the repo (an empty gitignored one exists).
- **Browser layer = CloakBrowser** (headless passes Cloudflare Turnstile on
  chatgpt.com; runs natively here, no Flatpak needed). The login profile lives
  at **`~/.local/share/chatgpt-proxy/profile`** (moved out of the repo in the
  refactor; override with `CHATGPT_PROXY_PROFILE`). Headless, no re-login.
- **Lifecycle (orphan-safe):** `browser.py` clears stale `Singleton*` locks on
  boot and, on shutdown (SIGTERM/SIGINT), closes the context and kills any
  profile-scoped Chrome — verified: SIGTERM leaves 0 orphan Chrome procs. If
  you ever `kill -9` it, just relaunch (boot cleans the stale lock).
- **Backend send endpoint = `POST /backend-api/f/conversation`** (SSE, `v1`
  delta encoding). It is gated by a **sentinel** anti-bot flow requiring, per
  request: `Authorization: Bearer` (from `/api/auth/session`) **plus** three
  browser-minted tokens — `openai-sentinel-chat-requirements-token`,
  `openai-sentinel-proof-token` (proof-of-work), and
  `openai-sentinel-turnstile-token` (**Cloudflare Turnstile**). Full schema +
  OpenAI mapping: `docs/discovery/2026-07-10-backend-api-capture.md`.
- **Consequence:** a pure server-side HTTP proxy can't mint the Turnstile
  token — the proxy must keep a CloakBrowser session in the loop.
- **Decisions made (2026-07-10), build to these:**
  1. **Architecture = browser-backed.** The OpenAI-compatible HTTP server
     wraps a live authenticated CloakBrowser session; the browser mints the
     sentinel/Turnstile/PoW tokens (trigger a real send + capture the SSE via
     CDP). Not pure-HTTP, not hybrid (hybrid is a possible later optimization).
  2. **History = stateful.** Keep a ChatGPT `conversation_id` +
     `parent_message_id` and send only the newest user message per call
     (don't replay full history). Bridge OpenAI's stateless `messages[]` onto
     one persistent ChatGPT conversation.
  3. **Models = real ChatGPT slugs only.** Expose exactly what
     `GET /backend-api/models` returns (`gpt-5-3`, `auto`, ...); no aliasing
     to OpenAI names.
- **Secrets discipline:** capture files and committed `samples/` are redacted
  (tokens, cookies, `sessionToken`, email/PII, PoW blobs). Never commit the
  login profile or raw un-redacted captures; never log `sessionToken` or
  `accessToken`.

### Browser layer: CloakBrowser (decided 2026-07-10)

**The browser layer is CloakBrowser**, a stealth Chromium (C++ source-level
fingerprint patches) that is a **drop-in Playwright/Puppeteer replacement**
and auto-downloads its own binary. Reason: plain (Flatpak) Chrome **headless
is blocked by Cloudflare Turnstile** on chatgpt.com, but **CloakBrowser
headless passes it** (verified — see `docs/discovery/2026-07-10-cloakbrowser.md`).
It also runs natively on this flatpak-only machine (no missing libs) and
matches the existing Playwright-based Python code.

- Use `from cloakbrowser import launch, launch_persistent_context`.
  `launch_persistent_context("./cloak-profile", headless=...)` gives
  persistent cookies (log in once, reuse headless after).
- Setup + reproducible commands are in the CloakBrowser discovery doc. Clone
  lives at `~/projects/CloakBrowser`; a venv there has it installed; the
  stealth binary is cached at `~/.cloakbrowser/`.
- The Flatpak-Chrome + `chrome-devtools-mcp` CLI path below is now
  **secondary/fallback** (kept for ad-hoc interactive inspection — you can
  attach it to CloakBrowser via `launch(args=["--remote-debugging-port=9242"])`
  then `chrome-devtools start --browserUrl http://127.0.0.1:9242`). Prefer
  CloakBrowser/Playwright for anything the project ships.

### Discovery workflow for this project

1. **Check existing code/docs first** before re-deriving anything: read
   `chatgpt_api_server.py`, `manual_login.py`, `go-api/`, and everything
   under `docs/discovery/` (below) for prior findings.
2. **Drive a real, authenticated browser session** with **CloakBrowser**
   (persistent context, log in once) to chatgpt.com, use the UI once logged
   in, and capture the underlying network traffic (Playwright
   `page.on("response")` / a CDP `Network`/`Fetch` session for the streaming
   SSE body; or attach `chrome-devtools-mcp` to the CDP port) — request +
   response bodies, headers, especially auth headers/cookies/any
   proof-of-work or device-check tokens — while a real message is sent and
   streamed back.
3. **Record every discovery in `docs/discovery/`** as it's found — endpoint
   URLs, required headers, request/response JSON shapes, streaming format
   (SSE event structure), auth/session requirements, any anti-automation
   challenge (e.g. Cloudflare/turnstile, proof-of-work headers like
   `Openai-Sentinel-*`), and rate-limit behavior. Write these up
   incrementally, not just at the end — if a session gets interrupted, the
   next session must be able to pick up from the docs without re-doing the
   discovery work.
4. **Document the process itself, not just the result.** When a working
   pattern is found (e.g. "start browser with real cookies this way", "this
   header is required or the request 403s", "this is how streaming chunks
   are framed", "this loophole/workaround got past X restriction"), write
   it down as its own dated entry so future sessions know both *what works*
   and *how it was figured out* — avoid rediscovering the same dead ends.
5. Suggested layout: `docs/discovery/README.md` as an index, plus one file
   per topic/session (e.g. `docs/discovery/2026-07-10-network-capture.md`).
   Keep raw captured request/response samples (sanitized of secrets) inline
   or as small fixture files alongside the notes.

## Chrome browser automation setup (chrome-devtools-mcp CLI)

This machine has no `claude-in-chrome` MCP tools registered in the session tool
list (checked and confirmed absent — do not waste time on `ToolSearch` for
`mcp__claude-in-chrome__*` here). Browser automation must go through the
**`chrome-devtools-mcp` CLI** (`chrome-devtools <tool>`), documented by the
`chrome-devtools-mcp` plugin skill. Read that skill's SKILL.md for command
syntax; this section only covers machine-specific setup that isn't in the
skill docs.

### One-time facts about this machine

- `chrome-devtools` CLI binary is **not installed by default**. Install with:
  `npm i -g chrome-devtools-mcp@latest`, then verify with `chrome-devtools status`.
- There is **no native Chrome/Chromium binary** on this machine (no
  `/opt/google/chrome/chrome`, no `google-chrome`/`chromium` on PATH).
- Chrome **is** installed as a **Flatpak**: `com.google.Chrome` (app id).
  - Do NOT invoke the flatpak's internal binary directly (e.g. the path under
    `~/.local/share/flatpak/app/com.google.Chrome/.../files/bin/chrome`) — it
    is a sandbox-internal wrapper script and fails outside the flatpak sandbox
    (`exec: cobalt: not found`, permission errors writing to `/etc/opt/chrome`).
  - Always launch it via `flatpak run com.google.Chrome ...args...`.

### Standard workflow: clean/isolated headless instance (default, safe)

```bash
# 1. Launch flatpak Chrome headless with an isolated temp profile + remote debugging port
flatpak run com.google.Chrome --headless --remote-debugging-port=9333 \
  --no-sandbox --user-data-dir=/tmp/chrome-profile-<unique> about:blank \
  > /tmp/chrome_flatpak.log 2>&1 &
disown

# 2. Verify the debugging endpoint is up
curl -s http://127.0.0.1:9333/json/version

# 3. Point the chrome-devtools-mcp daemon at it
chrome-devtools start --browserUrl http://127.0.0.1:9333
chrome-devtools status
chrome-devtools list_pages
```

This gives a completely clean session: no cookies, no saved logins, no
history/extensions from the user's real browsing. This is the **default**
choice unless the user explicitly asks for their real logged-in session.

To stop: `chrome-devtools stop` then `pkill -f "remote-debugging-port=9333"`.

### The user's real Chrome profile (cookies/logins/history)

The Flatpak Chrome app's real profile (used interactively by the user) lives
at:

```
~/.var/app/com.google.Chrome/config/google-chrome/Default/
```

It contains real `Cookies`, `Login Data`, `History`, etc. (verified present
and recently modified, i.e. actively used).

**Important restriction found:** Chrome refuses to enable the remote
debugging port when pointed at its own default user-data-dir path, printing:
`DevTools remote debugging requires a non-default data directory.` So you
cannot attach chrome-devtools-mcp directly to
`~/.var/app/com.google.Chrome/config/google-chrome` in place.

**Security boundary — do NOT copy the real profile to work around this.**
An attempt to `cp -a` the real profile (including `Cookies`/`Login Data`) into
`/tmp` was **blocked by the Claude Code auto-mode safety classifier**: copying
live credential/session files to an unprotected shared location (`/tmp`) is
treated as unauthorized PII/credential exposure, even when the user has
approved "use the real profile" in general terms. That approval does not
imply consent to duplicate credential stores onto disk elsewhere.

**If the user asks to automate with their real logged-in session, do this
instead of copying the profile:**
1. Explain the restriction above (remote debugging blocked on default dir;
   copying credentials elsewhere is not something to do without very explicit,
   specific authorization for that exact action).
2. Ask the user directly how they want to proceed. Reasonable options to
   present:
   - Attach to an **already-running** real Chrome window that the user starts
     themselves with remote debugging enabled (e.g. via `chrome://inspect` /
     `--autoConnect`, requires Chrome 144+), so the real profile's data never
     leaves its normal location.
   - Use the clean/isolated instance (above) and have the user manually log
     into whatever site is needed within that session — this creates fresh,
     scoped cookies rather than reusing the real profile.
   - If the user explicitly authorizes copying/duplicating the real profile
     data to a specific location, treat that as a distinct, narrow permission
     — do not generalize it to "always copy the profile" for future tasks.
3. Do not retry the copy-to-`/tmp` approach or attempt alternate ways to
   dump the same credential files (e.g. symlinking) without the user
   explicitly re-authorizing that specific action after understanding the
   risk.
