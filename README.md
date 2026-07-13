# webllm-proxy

A single **OpenAI-compatible** local server over **login-only web LLMs**, driven
through a stealth browser. For people who have a web login (ChatGPT, a Databricks
workspace, Microsoft Copilot) but no API key/budget, and want to point
OpenAI-SDK-compatible tools — coding agents, scripts — at it.

One server, one port, N providers. Every provider speaks the **same** OpenAI
surface (`/v1/chat/completions`, `/v1/models`) and exposes exactly two methods
internally (`models()`, `completions()`):

| Provider | Backend | Tools | Reasoning |
|---|---|---|---|
| `chatgpt` | chatgpt.com (GPT-5, ...) | emulated (tag contract) | `reasoning_effort` -> web `thinking_effort` |
| `databricks` | Databricks Genie / `llmproxy` (Claude Sonnet 4.5 on Bedrock; Azure GPT-4.1) | native (Claude) / passthrough (GPT) | native extended thinking |
| `copilot` | Microsoft Copilot (M365 BizChat) | emulated (tag contract) | model variations (`copilot__Reasoning`) |

Models from every enabled provider are merged onto one `/v1/models`, ids
namespaced `<provider>__<slug>` (e.g. `chatgpt__gpt-5-mini`,
`databricks__claude-4-5-sonnet`, `copilot__Reasoning`). Requests route by that
prefix.

## How it works

Each provider wraps a persistent, logged-in
**[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)** session (a stealth
Chromium that passes Cloudflare Turnstile; auto-downloads its own binary). The
`gateways/cloakbrowser` session runs the page on one worker thread and captures
the relevant network response over the Chrome DevTools Protocol; the provider
supplies a `trigger`, a `capture_url` predicate, and a `parse` accumulator per
turn.

- **chatgpt** — types the prompt into the composer (the frontend mints the
  single-use Turnstile/PoW token), captures the `backend-api/f/conversation`
  SSE, translates the `v1` delta encoding into OpenAI chunks. Model + reasoning
  effort are forced by rewriting the request body via CDP `Fetch`. Function
  calling is emulated via a `<tool>`/`<assistant>` tag contract.
- **databricks** — issues the `llmproxy` fetch in-page (httpOnly cookie
  auto-attaches; CSRF from `/auth/session/info` never leaves the browser).
  Claude ids convert the OpenAI request to Anthropic Messages and convert the
  native Anthropic SSE back; GPT ids pass through the Azure OpenAI channel.
- **copilot** — types into the M365 BizChat composer and captures the ChatHub
  SignalR WebSocket frames. Function calling is emulated (same tag contract).

## Install

Requires Python >= 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                     # create .venv, install deps + this package
uv run webllm-proxy install # pre-download the stealth browser (~200MB; optional)
```

## Configure

Copy/edit `webllm-proxy.yaml` at the repo root:

```yaml
server:
  host: 127.0.0.1
  port: 5100
providers:
  chatgpt:
    enabled: true
    headless: true
  databricks:
    enabled: false
    workspace_url: "https://dbc-xxxx.cloud.databricks.com/?o=1234567890"
    models: [claude-4-5-sonnet]
    openai_models: [gpt-41-2025-04-14, gpt-41-mini-2025-04-14]
  copilot:
    enabled: false
    edition: m365
```

## Run

Log in once per enabled provider (headed, needs a display), then serve them all
on one port:

```bash
uv run webllm-proxy login --provider chatgpt          # once, headed
uv run webllm-proxy serve --config-file ./webllm-proxy.yaml
```

```bash
curl -s http://127.0.0.1:5100/v1/models
curl -N http://127.0.0.1:5100/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"chatgpt__gpt-5-mini","stream":true,
       "messages":[{"role":"user","content":"Count to 5"}]}'
```

Research is a model, not a separate API: send `model: "chatgpt__research"` for a
long, web-search-backed, structured-markdown answer.

## Use with the OpenAI SDK / `pi`

Any OpenAI-compatible client points at `http://127.0.0.1:5100/v1` with a
namespaced model id. `pi` consumes the OpenAI format natively — add one provider
in `~/.pi/agent/models.json` pointing at the unified endpoint and list the
`<provider>__<slug>` ids you want.

## Configuration reference

The YAML config is the source of truth (parsed with pyyaml, validated with
pydantic — see `webllm_proxy/utils/config.py`). A few debug env vars remain:
`WEBLLM_PROXY_DUMP_SSE` (dump raw captured SSE to a file),
`WEBLLM_PROXY_DUMP_DIR` (where redacted `*_last_request.json` dumps land).

## Design & known limitations

- **Browser-backed**: a pure HTTP reimplementation isn't feasible for chatgpt
  (per-request Turnstile/PoW). databricks could be mostly server-side, but
  reuses the same transport for now.
- **Emulated function calling (chatgpt, copilot)** via a tag prompt contract —
  reliability is model-dependent. **databricks** Claude is native; there are no
  native-channel interception tricks anymore (chatgpt's internal tool messages
  are ignored).
- **Serialized**: one turn at a time per provider (single browser each).
- `usage` is currently zero everywhere (token counting deferred).
- Automates a web app you're logged into — likely against ToS beyond personal use.

## Architecture map

Exactly five folders under `webllm_proxy/`, each a clear responsibility:

```
webllm_proxy/
  cli.py, server.py     argparse CLI (serve|login|install) + composition root
  http/                 one Flask app + controllers (models, chat/completions,
                        health), decoupled from providers
  gateways/
    cloakbrowser/       the browser session/transport + login (run_turn/evaluate)
  providers/
    base.py             the 2-method Provider protocol
    chatgpt/            models() + completions(); v1 SSE parser; research model
    databricks/         Claude (convert) + Azure GPT channels; llmproxy envelope
    copilot/            M365 BizChat; SignalR decode
  prompts/system_prompts/*.md   every injected prompt, as a .md file
  utils/                config (yaml+pydantic), env/logging/redaction/process,
                        openai wire, openai<->anthropic convert, tag contract,
                        token counting
tests/                  browser-free unit tests seeded from real captures +
                        an OpenAI-SDK smoke suite (skippable, needs a live server)
docs/discovery/         how each web backend was reverse-engineered
```

## Development

```bash
uv run poe check      # fmt + lint (ruff, strict) + typecheck (ty) + test (pytest)
uv run poe release    # check + build (uv build); publish is separate/manual
```

The `openai` / `anthropic` SDKs are dev-only, used purely as validation clients
in `tests/smoke_openai_sdk.py` to prove SDK compatibility across tools, thinking,
effort, roles, and streaming — never in the runtime path.

## Corporate / air-gapped install

CloakBrowser's binary download (~200MB) is the one thing needing internet beyond
PyPI, and the one most likely blocked by a TLS-inspecting corporate proxy or an
air-gapped policy. Three options:

1. **Internal mirror** — point `CLOAKBROWSER_DOWNLOAD_URL` at a mirror; also set
   `HTTPS_PROXY`/`HTTP_PROXY` and `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` (your root
   CA) if the gateway does TLS inspection.
2. **Pre-staged binary** — set `CLOAKBROWSER_BINARY_PATH`; `webllm-proxy install`
   then skips the download.
3. **Offline bundle** — on a connected machine `uv run poe bundle` (or
   `bundle-linux` / `bundle-windows`) collects wheels + the CloakBrowser binary
   into `dist/offline/` with an install script for the target machine.

If external clients (`pi`, `curl`, the SDKs) run behind a proxy, keep local
traffic direct: `export NO_PROXY=127.0.0.1,localhost`.

## Docs

`docs/discovery/` documents the reverse-engineering of each backend (the ChatGPT
web API + anti-bot flow, the Databricks llmproxy channel + model enumeration, the
Copilot ChatHub protocol), including the process, not just the result.
