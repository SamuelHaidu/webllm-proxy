# chatgpt-web-proxy

A local HTTP server that speaks the **OpenAI API** (`/v1/chat/completions`,
`/v1/models`) but is backed by a real, logged-in **ChatGPT web** session driven
through a stealth browser. For people who have a ChatGPT login but no OpenAI
API key/budget (e.g. locked-down enterprise environments) and want to point
OpenAI-compatible tools — coding agents, scripts — at it.

**Status:** working proxy — SSE streaming, model selection, thinking models,
stateful conversations, **OpenAI-style function/tool calling** (emulated), and
**native ChatGPT web search** (its citation markup is cleaned into markdown
links).

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
# tools work: pi drives its own read/bash/edit/write through the proxy
pi -p --provider chatgpt --model gpt-5-mini "How many .py files are in src/? Use a tool to check."
```

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
  conversation and forwards only what's new each call (a diverging history
  starts a fresh conversation). The system prompt + tool contract are injected
  on the first turn.
- **Function calling is emulated**: ChatGPT web has no client-facing
  function-calling API, so `tools` are injected as a prompt contract and the
  model's `tool_call` block is parsed back into OpenAI `tool_calls`
  (`finish_reason:"tool_calls"`). Tool-enabled requests are buffered (not
  token-streamed). The contract asks for **one tool call per reply** (the proxy
  is serialized), so parallel tool calls are discouraged.
- **Native web search** works and its citation markup is stripped/converted to
  markdown links; `cite` footnote links are dropped.
- **Reasoning effort**: OpenAI `reasoning_effort` (`minimal|low|medium|high`, or
  `reasoning.effort`) maps to ChatGPT web's `thinking_effort` ladder
  (`min|standard|extended|max`) and is injected into the request — but only for
  models whose `/backend-api/models` entry has `configurable_thinking_effort`
  (a no-op on accounts/models that don't advertise it).
- **Real slugs only**: `/v1/models` returns what ChatGPT exposes; no aliasing.
- **Serialized**: one turn at a time (single browser). `usage` counts are zero.
- Automates the ChatGPT web app — likely against OpenAI ToS beyond personal use.

## Layout

```
src/chatgpt_proxy/
  __main__.py   CLI: serve | login | install
  browser.py    CloakBrowser session (CDP capture, model override, lifecycle)
  sse.py        ChatGPT v1 delta-encoding parser -> content/reasoning (+citation cleanup)
  server.py     Flask app: OpenAI schema + stateful conversation mapping
  tools.py      emulated OpenAI function calling (contract + parser)
  config.py     env-driven config
docs/discovery/ how the ChatGPT web API was reverse-engineered (findings)
```

## Docs

`docs/discovery/` documents the reverse-engineering: how the browser layer was
chosen, the backend API + anti-bot flow, the streaming format, and the build.
