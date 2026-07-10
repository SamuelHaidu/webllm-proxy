# 2026-07-10 — Refactor to a lean uv package + orphan-safe browser lifecycle

Consolidated everything into one clean, full-Python `uv` package and fixed the
browser process management that was causing orphaned Chrome + stale locks.

## Removed (rubbish / superseded)

- `go-api/` (Go/CDP port) — removed per request.
- `chatgpt_api_server.py`, `manual_login.py`, `test_client.py` — old
  DOM-scraping Flask prototype.
- `frontend/` — React UI (already split to its own repo; only stale `.vite`
  cache remained).
- `requirements.txt` — replaced by `pyproject.toml`.
- `openai_proxy/` — refactored into `src/chatgpt_proxy/`.

## New layout (uv package `chatgpt-web-proxy`)

```
pyproject.toml              hatchling build; deps: cloakbrowser, flask, flask-cors
uv.lock                     locked deps
src/chatgpt_proxy/
  __main__.py   CLI: serve (default) | login | install  + graceful shutdown
  config.py     env-driven config (profile dir, host/port, headless, debug)
  browser.py    CloakBrowser session: worker thread, CDP capture, model
                override, and lifecycle (lock cleanup + orphan-safe shutdown)
  sse.py        v1 delta-encoding parser -> content/reasoning
  server.py     create_app(session): OpenAI schema + stateful mapping
```

Run: `uv sync` → `uv run chatgpt-proxy`. Console script: `chatgpt-proxy`.
The browser is a normal Python dependency (`cloakbrowser`); its ~200MB binary
downloads on first run or via `chatgpt-proxy install`.

## Profile moved out of the repo

The login profile now lives at **`~/.local/share/chatgpt-proxy/profile`**
(XDG data dir; override `CHATGPT_PROXY_PROFILE`) instead of `./.cloak-profile`.
Keeps the repo clean and the credential store in a private per-user location.

## Orphan-safe browser lifecycle (the main fix)

Previously, killing the server left Chrome orphaned, an EPIPE in the node
driver, and a stale `SingletonLock` that blocked the next launch. Now
(`browser.py`):

- **On boot:** delete stale `Singleton*` lock files in the profile before
  launch → recovers automatically from a prior hard kill.
- **On shutdown (SIGTERM/SIGINT):** the worker thread closes the Playwright
  context, then `kill_profile_chrome()` terminates any remaining Chrome whose
  cmdline references our profile dir (found by scanning `/proc`).
- `BrowserSession.close()` is idempotent and safe from any thread; `__main__`
  wires it to a SIGTERM handler and the `app.run` finally-block.

**Verified:** with 8 Chrome procs running, `SIGTERM` to `chatgpt-proxy` →
server exits cleanly, **0 orphan Chrome processes** left. (If you still
`kill -9`, the boot-time lock cleanup makes the next start succeed.)

## Env gotcha

The shell here sets `UV_ENV_FILE=.env`, so `uv run` fails without a `.env`
file. An empty, gitignored `.env` is committed-around (ignored) so `uv run`
works. Alternatively run the console script directly: `.venv/bin/chatgpt-proxy`.

## Verified after refactor

`uv run chatgpt-proxy` (headless, moved profile, no DISPLAY) → `/v1/models`
lists the 8 slugs, `/v1/chat/completions` returns correct output
(`model=gpt-5-mini`, content `packaged`). All prior features intact.
