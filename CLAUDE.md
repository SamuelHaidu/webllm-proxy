# Project Notes for Claude Code

## Project intent

**`webllm-proxy`** (`src/webllm_proxy/`, a `uv` package) is a local API bridge
over **login-only web LLMs**, driven through a stealth browser — so coding
agents / tools built against a standard LLM API work for people who have a web
login but no official API key/budget. Provider/adapter architecture; two
providers ship today:

- `chatgpt` — chatgpt.com → OpenAI `/v1/chat/completions` (port 5102)
- `databricks` — Databricks Genie/`llmproxy` → Anthropic `/v1/messages` (port 5103)

CLI: `webllm-proxy serve|login|install --provider chatgpt|databricks`.

## pi integration (`integrations/pi/`)

A TypeScript **pi** package (`@earendil-works/pi-coding-agent`) that surfaces this
proxy inside the `pi` coding agent: it registers a `webllm` provider whose models
are auto-discovered from the **aggregator gateway** and adds agent tools. Start
the gateway with `webllm-proxy gateway` (default `:5100`); it fronts every
running per-provider proxy on one OpenAI/Anthropic surface, merging their
`/v1/models` (ids namespaced `<provider>__<slug>`) and routing by that prefix
(`webllm_proxy/gateway/`, a pure forwarder -- no browser). pi API/SDK reference
map: `docs/pi/pi-extension-sdk-index.md`. Full roadmap lives in the plan file +
`integrations/pi/README.md`.

Started 2026-07-10 as a ChatGPT-only tool (`chatgpt-web-proxy`); unified into
the current multi-provider shape the same day. Earlier DOM-scraping prototypes
(`chatgpt_api_server.py`, `manual_login.py`, a Go/CDP port, a React frontend)
were **removed** in that refactor — don't look for them, they're gone.

## Findings index — read this first, every session

**`docs/discovery/README.md` indexes every reverse-engineering finding**, one
dated entry per topic/session with a one-line summary (newest first). Read it
before doing any discovery work — it tells you what's already been tried, what
worked, and what's still open, so you don't redo work or rediscover dead ends.
**New findings go there** (one file per topic, dated), not in this file.

Quick map of what's covered where:
- ChatGPT backend API, anti-bot flow, SSE format:
  `2026-07-10-backend-api-capture.md`, `2026-07-10-v1-proxy.md`,
  `2026-07-10-browser-backed-validation.md`
- Tool/function calling, both backends (contract + native-channel
  interception for chatgpt, native for databricks): `2026-07-10-tool-calling.md`
- Reasoning effort mapping (OpenAI `reasoning_effort` ↔ web `thinking_effort`):
  `2026-07-10-thinking-effort.md`
- Databricks `llmproxy` backend (the whole thing — HAR analysis, live
  validation, model registry, auth): `2026-07-10-databricks-llmproxy.md`
- Browser layer choice + setup: `2026-07-10-cloakbrowser.md`,
  `2026-07-10-setup-and-cloudflare.md`
- Packaging/refactor history: `2026-07-10-refactor-packaging.md`

## Operational notes (not duplicated in the discovery docs)

- Shell sets `UV_ENV_FILE=.env`; `uv run` needs a `.env` in the repo (an empty
  gitignored one exists).
- **Secrets discipline:** never commit a login profile, a raw un-redacted
  capture, or a HAR file (`*.har` / `docs/databricks_har/` are gitignored);
  never log `sessionToken`/`accessToken`/cookies. Redact before committing any
  sample. `scripts/har_explore.py` is built to help with this (see below).
- Browser lifecycle is orphan-safe: boot clears stale `Singleton*` locks,
  shutdown (SIGTERM) closes the context and kills profile-scoped Chrome
  (verified 0 orphans). `kill -9` is fine too — next boot cleans the lock.
- Both providers are browser-backed (one CloakBrowser worker thread, see
  `core/browser.py`) but for different reasons: `chatgpt` needs the browser to
  mint per-request Turnstile/PoW tokens; `databricks` has no anti-bot check at
  all (a cookie-only server-side proxy would work) but reuses the same
  transport for now.

## Discovery workflow for new backends/features

1. Check `docs/discovery/README.md` first — don't re-derive anything already found.
2. If a HAR capture is available (gitignored, e.g. under `docs/*_har/`),
   explore it **before** opening a browser: `python scripts/har_explore.py
   <file> paths|list|show|req|resp|keys|grep`. It parses the whole HAR
   in-process but only ever prints small, secret-redacted summaries — read the
   tool's own docstring for the subcommands. This tells you what to expect
   before spending a live session on it (this is how the Databricks backend
   was scoped before ever opening a browser).
3. Drive a real, authenticated browser (CloakBrowser, persistent profile — log
   in once) to fill in what the HAR/docs don't answer: headers, auth flow,
   streaming format, anti-bot challenges, rate limits, while a real request is
   sent and captured.
4. Record every discovery in `docs/discovery/` **as you go**, not just at the
   end — one dated file per topic, sanitized samples alongside. Document the
   *process* (how you figured it out), not just the result, and add the entry
   to `docs/discovery/README.md`'s index.

## Browser layer: CloakBrowser

Stealth Chromium, drop-in Playwright/Puppeteer replacement, passes Cloudflare
Turnstile **headless** (plain Flatpak Chrome headless does not). Persistent
profile per provider (`~/.local/share/{chatgpt,databricks}-proxy/profile`);
log in once via `webllm-proxy login --provider <name>`, headless after.
Details/setup: `docs/discovery/2026-07-10-cloakbrowser.md`.

## Chrome browser automation for ad-hoc/manual inspection

(This is for interactive discovery/debugging sessions — **not** what the
shipped proxy uses; the proxy drives CloakBrowser directly in Python.)

- This machine has the **`chrome-devtools-mcp` CLI** (`chrome-devtools <tool>`
  via Bash). Don't assume `mcp__claude-in-chrome__*` tools are callable —
  check per session (they were not available as callable functions in this
  project's sessions so far, despite the harness sometimes surfacing their
  docs). See the `chrome-devtools-mcp:chrome-devtools-cli` skill for full
  command syntax. Handy for this project: `list_network_requests` /
  `get_network_request` (quick manual traffic inspection) and
  `evaluate_script` (ad-hoc in-page `fetch()` probes) as lighter alternatives
  to a one-off Python/CDP script.
- No native Chrome binary; Chrome is the `com.google.Chrome` **Flatpak** —
  launch with `flatpak run com.google.Chrome ...`, never the internal binary
  path directly (fails outside the sandbox).
- Standard recipe: headless Flatpak Chrome with an isolated
  `--user-data-dir=/tmp/...` + `--remote-debugging-port=<port>`, then
  `chrome-devtools start --browserUrl http://127.0.0.1:<port>`. Clean,
  cookie-free — the default choice.
- `chrome-devtools status` can report the daemon "running" while its
  underlying Chrome process is actually dead — confirm with
  `curl http://127.0.0.1:<port>/json/version` too, don't trust `status` alone.

### The user's real Chrome profile — do NOT copy it

Real profile: `~/.var/app/com.google.Chrome/config/google-chrome/Default/`
(live `Cookies`/`Login Data`). Chrome refuses remote debugging on this default
dir. **Do not copy this profile anywhere (e.g. `/tmp`) to work around that** —
duplicating live credential files to a shared location is a hard no without
explicit, specific user authorization for that exact action, even if the user
approved "use my real profile" in general terms. If the user wants their real
session automated, ask how: attach to an already-running instance *they*
start with debugging enabled, or use a clean instance and have them log in
fresh there.
