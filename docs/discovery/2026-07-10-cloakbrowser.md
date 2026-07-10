# 2026-07-10 — CloakBrowser: stealth browser layer (VALID FRAMEWORK)

**Headline result:** CloakBrowser in **headless** mode **passes Cloudflare
Turnstile on chatgpt.com** — the full ChatGPT app renders, no "Just a
moment..." challenge. This is exactly what plain Flatpak Chrome headless
could **not** do (see `2026-07-10-setup-and-cloudflare.md`). CloakBrowser is
therefore adopted as the browser layer for this project.

## What CloakBrowser is

- A **stealth Chromium binary** with fingerprints patched at the C++ source
  level (58 patches on the free v146 build; 66 on Pro v148). Not JS injection,
  not config flags — the anti-bot surface is baked into the binary, so
  detectors score it as a real browser.
- A **drop-in Playwright/Puppeteer replacement** for Python and JS. Same API;
  swap the import.
- **Auto-downloads its own Chromium binary** (~206MB) — no system Chrome
  needed. This sidesteps the whole Flatpak-Chrome mess on this machine.
- Repo: https://github.com/CloakHQ/CloakBrowser (MIT wrapper). Cloned to
  `~/projects/CloakBrowser` (sibling of this repo, for reference/inspection).
- Free binary (v146) is on their CDN/GitHub releases, no license. Pro (v148,
  latest patches) needs `CLOAKBROWSER_LICENSE_KEY` (paid; 7-day trial). Free
  "goes stale within weeks" as detection evolves, per their README.

## Verified setup on THIS machine (reproducible)

All network steps need the Bash sandbox disabled (clone / pip / 206MB binary
download).

```bash
# 1. Clone (sibling dir, keeps our repo clean)
cd ~/projects && git clone --depth=1 https://github.com/CloakHQ/CloakBrowser.git

# 2. venv + install the wrapper (editable from the clone; or `pip install cloakbrowser`)
cd ~/projects/CloakBrowser
python3 -m venv .venv
.venv/bin/pip install -e .          # pulls playwright, httpx, cryptography
#   NOTE: do NOT need `playwright install chromium` — CloakBrowser uses its
#   own downloaded binary.

# 3. Download + verify the stealth binary (206MB, Ed25519 sig + SHA-256 checked)
.venv/bin/python -m cloakbrowser install
#   -> ~/.cloakbrowser/chromium-146.0.7680.177.5/chrome

# 4. Diagnose (launch test + Linux missing-lib check)
.venv/bin/python -m cloakbrowser info
#   -> "Launch: ✓ Chromium 146.0.7680.177"  ← binary runs natively, no missing libs
```

### Runtime facts found

- **Binary launches natively on this flatpak-only machine** — no missing
  shared libraries reported by `info`. So we do NOT need Flatpak or the system
  Chrome for CloakBrowser. Binary lives at
  `~/.cloakbrowser/chromium-146.0.7680.177.5/chrome`.
- Python 3.13.11 (pyenv), venv, pip 25.3, Docker 29.6.1 all present. Docker
  image `cloakhq/cloakbrowser` is a fallback if native launch ever breaks.
- Free binary version `146.0.7680.177.5` — same v146 major as the Flatpak
  Chrome, so behavior is familiar.
- `info` reports missing: Windows/Office **fonts** (only matter for
  CreepJS/Kasada canvas-font hashing, **not** Cloudflare/ChatGPT), and
  optional modules `geoip2` (proxy geoip), `aiohttp`/`websockets` (only for
  `cloakserve` CDP-server mode). **None needed** for our use case (real login,
  no proxy).

## Verified: headless passes Turnstile

Test script `/tmp/cloak_turnstile_test.py` → `launch(headless=True)` →
`goto("https://chatgpt.com")`:

- final title: `'ChatGPT'` (not "Just a moment...")
- verdict: **PASSED** — real logged-out ChatGPT UI rendered (composer +
  sidebar), screenshot `/tmp/cloak_headless.png`.
- No proxy, no humanize, no fonts — plain `launch(headless=True)` was enough
  for ChatGPT's Cloudflare Turnstile. (README warns some *harder* sites still
  detect headless; ChatGPT is not one of them, verified.)

## How this integrates with our flow

CloakBrowser **replaces Flatpak Chrome + chrome-devtools-mcp** as the browser
layer for discovery and the eventual proxy. It is a Playwright drop-in, which
also matches the existing Python code (`chatgpt_api_server.py` already uses
Playwright).

- **Cookies / stay logged in:** `launch_persistent_context("./cloak-profile",
  headless=...)`. Log in once (headed), reused headless afterwards. Persists
  cookies + localStorage across runs (this is the clean replacement for the
  Flatpak persistent-profile approach — and it passes Turnstile).
- **Network capture (for the OpenAI-schema proxy discovery):** either
  Playwright (`page.on("response")`, or a CDP session `Network`/`Fetch`
  domain for the streaming SSE body), **or** attach our existing
  `chrome-devtools-mcp` CLI by launching with a debug port:
  `launch(args=["--remote-debugging-port=9242"])` then
  `chrome-devtools start --browserUrl http://127.0.0.1:9242`. Stealth flags
  are already applied to that browser.
- **Env knobs:** `CLOAKBROWSER_LICENSE_KEY` (Pro), `CLOAKBROWSER_BINARY_PATH`
  (use a local build), `CLOAKBROWSER_DOWNLOAD_URL` (self-host).

## Pending / next

- [ ] Log in **once** (headed CloakBrowser, persistent profile
      `./cloak-profile`) — replaces the flaky Flatpak login attempts.
- [ ] With the authenticated session, send one message and capture
      `backend-api/conversation`: auth header (Bearer JWT from
      `/api/auth/session`?), request JSON, SSE event framing, any
      `Openai-Sentinel-*` proof-of-work / device-check headers.
- [ ] Decide binary tier (Free v146 vs Pro v148) — see open decision.
