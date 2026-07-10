# chatgpt-web-proxy

A local HTTP server that speaks the **OpenAI API** (`/v1/chat/completions`,
`/v1/models`) but is backed by a real, logged-in **ChatGPT web** session driven
through a stealth browser. For people who have a ChatGPT login but no OpenAI
API key/budget (e.g. locked-down enterprise environments) and want to point
OpenAI-compatible tools — coding agents, scripts — at it.

**Status:** working minimal proxy — SSE streaming, model selection, thinking
models, and stateful conversations. Tool/function calling is not implemented
yet (next milestone).

## How it works

The ChatGPT send endpoint is gated by a single-use Cloudflare Turnstile token +
a proof-of-work token that only a browser can mint, so this is **browser-backed**
(a pure HTTP reimplementation isn't feasible — see `docs/discovery/`):

1. Keep a persistent, logged-in **[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)**
   session (headless). CloakBrowser is a stealth Chromium that passes Cloudflare
   Turnstile; it auto-downloads its own browser binary.
2. Per request, type the prompt into the composer — the ChatGPT frontend mints
   the anti-bot tokens and issues the real `backend-api/f/conversation` call.
3. Capture that SSE response over the Chrome DevTools Protocol
   (`Network.streamResourceContent`) and translate the `v1` delta encoding into
   OpenAI streaming chunks. Model selection rewrites the request body's `model`
   via the CDP `Fetch` domain.

Everything is Python; the browser is a normal Python dependency
(`cloakbrowser`) whose binary downloads on first run.

## Install

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                      # create .venv, install deps + this package
uv run chatgpt-proxy install # pre-download the stealth browser (~200MB; optional)
```

## One-time login

```bash
uv run chatgpt-proxy login   # opens a browser window; log in to ChatGPT once
```

The session persists in `~/.local/share/chatgpt-proxy/profile` (override with
`CHATGPT_PROXY_PROFILE`). `login` needs a display; the server afterwards runs
headless.

## Run

```bash
uv run chatgpt-proxy          # serve on http://127.0.0.1:5102
```

```bash
curl -s http://127.0.0.1:5102/v1/models
curl -N http://127.0.0.1:5102/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5-mini","stream":true,"messages":[{"role":"user","content":"Count to 5"}]}'
```

Point any OpenAI-compatible client at `http://127.0.0.1:5102/v1` with any dummy
API key.

## Use with the `pi` coding agent

Add a custom provider in `~/.pi/agent/models.json`:

```json
{
  "providers": {
    "chatgpt": {
      "baseUrl": "http://127.0.0.1:5102/v1",
      "api": "openai-completions",
      "apiKey": "chatgpt-web-proxy",
      "compat": { "supportsDeveloperRole": false, "supportsReasoningEffort": false },
      "models": [
        { "id": "gpt-5-mini", "reasoning": false, "input": ["text"],
          "contextWindow": 128000, "maxTokens": 32000,
          "cost": {"input":0,"output":0,"cacheRead":0,"cacheWrite":0} }
      ]
    }
  }
}
```

```bash
pi -p --no-tools --provider chatgpt --model gpt-5-mini "Write a Python add(a,b)."
```

Use `--no-tools` until tool calling lands (the proxy drops `tools` for now).

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `CHATGPT_PROXY_PROFILE` | `~/.local/share/chatgpt-proxy/profile` | login profile dir |
| `CHATGPT_PROXY_HEADLESS` | `1` | `0` to watch the browser |
| `CHATGPT_PROXY_HOST` | `127.0.0.1` | bind host |
| `CHATGPT_PROXY_PORT` | `5102` | port |
| `CHATGPT_PROXY_DEBUG_DUMP` | off | dump each request to `/tmp` |

## Design & known limitations

- **Stateful**: maps stateless OpenAI `messages[]` onto one ongoing ChatGPT
  conversation and sends only the newest user message. The system prompt is
  folded into the first turn and **dropped on follow-ups**; `tools` are dropped
  (both change with the tool-calling milestone).
- **Real slugs only**: `/v1/models` returns what ChatGPT exposes; no aliasing.
- **Serialized**: one turn at a time (single browser). `usage` counts are zero.
- Automates the ChatGPT web app — likely against OpenAI ToS beyond personal use.

## Layout

```
src/chatgpt_proxy/
  __main__.py   CLI: serve | login | install
  browser.py    CloakBrowser session (CDP capture, model override, lifecycle)
  sse.py        ChatGPT v1 delta-encoding parser -> content/reasoning
  server.py     Flask app: OpenAI schema + stateful conversation mapping
  config.py     env-driven config
docs/discovery/ how the ChatGPT web API was reverse-engineered (findings)
```

## Docs

`docs/discovery/` documents the reverse-engineering: how the browser layer was
chosen, the backend API + anti-bot flow, the streaming format, and the build.
