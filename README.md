# webllm-proxy

Local API bridges over **login-only web LLMs**, driven through a stealth
browser. For people who have a web login (ChatGPT, a Databricks workspace) but
no API key/budget, and want to point OpenAI-/Anthropic-compatible tools — coding
agents, scripts — at it.

One tool, multiple backends (**providers**):

| Provider | Backend | Exposes | Port |
|---|---|---|---|
| `chatgpt` | chatgpt.com (GPT-5, ...) | **OpenAI** `/v1/chat/completions`, `/v1/models` | 5102 |
| `databricks` | Databricks Genie / `llmproxy` (Claude Sonnet 4.5 on Bedrock) | **Anthropic** `/v1/messages`, `/v1/models` | 5103 |

`chatgpt` emulates function calling (a prompt contract) and native web search;
`databricks` is a near pass-through to the native Anthropic Messages API, so
tool calling and extended thinking are native.

## How it works

Both providers wrap a persistent, logged-in **[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)**
session (a stealth Chromium that passes Cloudflare Turnstile; auto-downloads its
own binary). A shared **core** runs the page on one worker thread and captures
the relevant network response over the Chrome DevTools Protocol; each provider
adapts the rest (see `src/webllm_proxy/providers/base.py`):

- **chatgpt** — the send endpoint is gated by a single-use Turnstile + PoW token
  only a browser can mint, so per request it types the prompt into the composer
  (the frontend mints the tokens), captures the `backend-api/f/conversation` SSE,
  and translates the `v1` delta encoding into OpenAI chunks. Model + reasoning
  effort are forced by rewriting the request body via CDP `Fetch`.
- **databricks** — no anti-bot token-minting, so per request it issues the
  `llmproxy` fetch **in-page** (the httpOnly session cookie auto-attaches; the
  CSRF token, read from `/auth/session/info`, never leaves the browser) and
  streams the native Anthropic SSE straight back.

## Install

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                     # create .venv, install deps + this package
uv run webllm-proxy install # pre-download the stealth browser (~200MB; optional)
```

## ChatGPT provider

```bash
uv run webllm-proxy login --provider chatgpt    # once, headed; log in to ChatGPT
uv run webllm-proxy serve --provider chatgpt    # OpenAI API on :5102
```

```bash
curl -s http://127.0.0.1:5102/v1/models
curl -N http://127.0.0.1:5102/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5-mini","stream":true,"messages":[{"role":"user","content":"Count to 5"}]}'
```

## Databricks provider

Set your workspace URL (with the `?o=<org-id>` query) so the browser knows where
to go and which org to send:

```bash
export DATABRICKS_PROXY_URL="https://dbc-xxxx.cloud.databricks.com/?o=1234567890"
uv run webllm-proxy login --provider databricks   # once, headed; log in to Databricks
uv run webllm-proxy serve --provider databricks   # Anthropic API on :5103
```

```bash
curl -s http://127.0.0.1:5103/v1/models
curl -N http://127.0.0.1:5103/v1/messages -H 'Content-Type: application/json' \
  -d '{"model":"claude-4-5-sonnet","max_tokens":128,"stream":true,
       "system":"You are a helpful assistant.",
       "messages":[{"role":"user","content":"Say hello."}]}'
```

Available models depend on your workspace entitlements; on the dev account
`claude-4-5-sonnet` is enabled (see `docs/discovery/2026-07-10-databricks-llmproxy.md`).

## Use with the `pi` coding agent

`~/.pi/agent/models.json` — add whichever provider(s) you run:

```json
{
  "providers": {
    "chatgpt": {
      "baseUrl": "http://127.0.0.1:5102/v1", "api": "openai-completions",
      "apiKey": "webllm-proxy",
      "compat": { "supportsDeveloperRole": false, "supportsReasoningEffort": false },
      "models": [ { "id": "gpt-5-mini", "reasoning": false, "input": ["text"],
        "contextWindow": 128000, "maxTokens": 32000,
        "cost": {"input":0,"output":0,"cacheRead":0,"cacheWrite":0} } ]
    },
    "databricks": {
      "baseUrl": "http://127.0.0.1:5103", "api": "anthropic-messages",
      "apiKey": "webllm-proxy",
      "models": [ { "id": "claude-4-5-sonnet", "reasoning": true, "input": ["text"],
        "contextWindow": 200000, "maxTokens": 64000,
        "cost": {"input":0,"output":0,"cacheRead":0,"cacheWrite":0} } ]
    }
  }
}
```

```bash
pi -p --provider databricks --model claude-4-5-sonnet "List the .py files in src/ using a tool."
```

## Configuration (env)

`chatgpt`: `CHATGPT_PROXY_PROFILE`, `CHATGPT_PROXY_HEADLESS` (`0` to watch),
`CHATGPT_PROXY_HOST`, `CHATGPT_PROXY_PORT`, `CHATGPT_PROXY_DEBUG_DUMP`.

`databricks`: `DATABRICKS_PROXY_URL` (required), `DATABRICKS_PROXY_PROFILE`,
`DATABRICKS_PROXY_HEADLESS`, `DATABRICKS_PROXY_HOST`, `DATABRICKS_PROXY_PORT`,
`DATABRICKS_PROXY_MODEL`, `DATABRICKS_PROXY_CLIENT_ID`, `DATABRICKS_PROXY_MODELS`.

Shared: `WEBLLM_PROXY_PROVIDER` (CLI default), `WEBLLM_PROXY_DUMP_SSE` (dump raw
captured SSE to a file, for debugging).

## Design & known limitations

- **Browser-backed**: a pure HTTP reimplementation isn't feasible for chatgpt
  (per-request Turnstile/PoW). databricks *could* be mostly server-side (cookie
  only), but reuses the same browser-backed transport for now.
- **chatgpt function calling is emulated** (a `<tool>`/`<assistant>` tag prompt
  contract — see `tools.py` — plus interception of ChatGPT's own native tool
  channel). Reliability is model-dependent. `databricks` tool calling is native.
- **Stateful (chatgpt)** vs **stateless pass-through (databricks)**.
- **Serialized**: one turn at a time (single browser). `usage` from chatgpt is
  zero; databricks passes through the real Anthropic usage.
- Automates a web app you're logged into — likely against ToS beyond personal use.

## Layout

```
src/webllm_proxy/
  __main__.py         unified CLI: serve | login | install  (--provider)
  server.py           Flask factory: /health + the provider's routes
  core/               provider-agnostic: browser transport (CDP capture), process/env
  providers/
    base.py           Provider interface + Accumulator/Job
    chatgpt/          OpenAI surface, v1 SSE parse, emulated tools, Fetch override
    databricks/       Anthropic /v1/messages pass-through to llmproxy
tests/                browser-free unit tests (parsers, accumulators, mapping)
docs/discovery/       how each web backend was reverse-engineered
```

## Docs

`docs/discovery/` documents the reverse-engineering of each backend (the ChatGPT
web API + anti-bot flow, the Databricks llmproxy channel + model enumeration),
including the process, not just the result.
