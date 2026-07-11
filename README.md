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
own binary). A shared **transport** runs the page on one worker thread and
captures the relevant network response over the Chrome DevTools Protocol; each
provider adapts the rest (see `webllm_proxy/domain/ports.py`'s `Provider`
interface):

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

## Research tool

An async job API for longer, web-research-style questions: submit a query,
poll for status, get back a structured markdown report. Backed by a chatgpt
session (`provider.research_backend()`); mounted automatically by `serve` when
the active provider supports it.

```bash
curl -s -X POST http://127.0.0.1:5102/v1/research \
  -H 'Content-Type: application/json' -d '{"query":"What is the toolz Python library used for?"}'
# -> {"id": "...", "status": "queued", ...}
curl -s http://127.0.0.1:5102/v1/research/<id>       # poll until status is succeeded/failed
curl -s http://127.0.0.1:5102/v1/research            # list jobs
curl -s -X DELETE http://127.0.0.1:5102/v1/research/<id>
```

Or let the CLI do the polling:

```bash
uv run webllm-proxy research "What is the toolz Python library used for?"
```

Today this always runs the **emulated** backend (a normal chat turn + a
research-style prompt asking for thorough web search and a structured
report) — it works on any account, including free-tier, and is the
guaranteed path. A **Deep Research** backend exists as a documented seam
(`webllm_proxy/research/backends/deep_research.py`) but ships as an honest
stub (`available()` is hardcoded `False`) rather than a guessed trigger field;
see `docs/discovery/2026-07-11-deep-research-scoping.md` for why and what a
future session needs to do to wire it up on an entitled account.

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
`DATABRICKS_PROXY_MODEL`, `DATABRICKS_PROXY_MODELS` (Anthropic channel),
`DATABRICKS_PROXY_OPENAI_MODELS` (Azure/GPT channel), `DATABRICKS_PROXY_CLIENT_ID`,
`DATABRICKS_PROXY_AGENT_NAME`, `DATABRICKS_PROXY_AZURE_CLIENT_ID`,
`DATABRICKS_PROXY_AZURE_API_VERSION`, `DATABRICKS_PROXY_STYLE_RULES` (`0` to
drop the terse-response-style system prompt addition), `DATABRICKS_PROXY_DEBUG_DUMP`.

Shared: `WEBLLM_PROXY_PROVIDER` (CLI default provider), `WEBLLM_PROXY_DUMP_SSE`
(dump raw captured SSE to a file, for debugging), `WEBLLM_PROXY_DUMP_DIR`
(where the redacted `*_last_request.json` debug dumps land when a provider's
`*_DEBUG_DUMP` flag is on; defaults to the OS temp dir).

CloakBrowser itself (binary install/location, not this package): see
"Corporate / air-gapped install" below.

## Design & known limitations

- **Browser-backed**: a pure HTTP reimplementation isn't feasible for chatgpt
  (per-request Turnstile/PoW). databricks *could* be mostly server-side (cookie
  only), but reuses the same browser-backed transport for now.
- **chatgpt function calling is emulated** (a `<tool>`/`<assistant>` tag prompt
  contract, `strategies/tool_calling/agentclip.py`, tried after intercepting
  ChatGPT's own native tool channel, `native_channel.py`). Reliability is
  model-dependent. `databricks` tool calling is native.
- **Stateful (chatgpt)** vs **stateless pass-through (databricks)**.
- **Serialized**: one turn at a time (single browser). `usage` from chatgpt is
  zero; databricks passes through the real Anthropic usage.
- Automates a web app you're logged into — likely against ToS beyond personal use.

## Architecture map

Flat package, pragmatic clean-architecture layering: `domain` depends on
nothing else here; `application` depends only on `domain`; every adapter
(`providers`, `strategies`, `research`, `wire`, `http`, `transport`, `infra`,
`prompts`) depends inward, never the other way. No DTO/mapper layer —
`http/*_routes.py` reads/writes the client's raw OpenAI/Anthropic JSON
directly; the one real internal dataclass (`ChatTurn`) is used where there's
an actual concept to carry, not a 1:1 wire-JSON copy.

```
webllm_proxy/
  cli.py, server.py        argparse CLI (serve|login|install|research);
                           build_app() composition root (DI wiring)
  domain/                  ports (Provider, Accumulator, ToolStrategy,
                           PromptStore, JobStore, ResearchBackend) +
                           dataclasses (ChatTurn, ResearchRequest/Job)
  application/             chat.py: conversation continuity + effort mapping;
                           research.py: submit/poll + the background scheduler
  providers/
    chatgpt/               browser hooks (composer, CDP Fetch body rewrite,
                           SSE capture), config, v1 delta parser
    databricks/            browser hooks, config, llmproxy/Azure envelopes
  strategies/tool_calling/ emulation for backends with no native tool_calls:
                           native_channel.py (intercept ChatGPT's own tool
                           channel) tried first, agentclip.py (tag-contract
                           prompt) as fallback
  research/
    backends/              emulated.py (ships first, works everywhere) and
                           deep_research.py (documented stub — see
                           docs/discovery/2026-07-11-deep-research-scoping.md)
    jobstore/              in-memory job store (submit -> poll lifecycle)
  wire/                    pure OpenAI/Anthropic SSE + JSON shaping, no
                           Flask/browser dependency
  http/                    thin(ish) Flask blueprints (openai_routes,
                           anthropic_routes, research_routes, health)
  transport/               BrowserSession (one worker thread, per-job
                           timeout) + cross-platform process/lock hygiene
  infra/                   env/data-dir helpers, logging + correlation ids,
                           token/secret redaction for debug dumps
  prompts/                 every prompt this tool injects, as a .md file
                           (loaded + cached by prompts/loader.py), not a
                           Python string constant
tests/                     browser-free unit tests (parsers, accumulators,
                           wire mapping, prompt loading, research scheduler)
docs/discovery/            how each web backend was reverse-engineered
docs/refactor/PROGRESS.md  the modularization effort's own tracking doc —
                           phase checklist + dated findings, useful context
                           if you're wondering "why is this built this way"
scripts/build_offline_bundle.py   see "Corporate / air-gapped install" below
```

## Development

`uv sync` (see Install above) already pulls the dev tools (ruff, ty, pytest,
poethepoet) — they're a default dependency group, no extra flag needed.

```bash
uv run poe check      # fmt + lint (ruff, strict) + typecheck (ty) + test (pytest)
uv run poe release    # check + build (uv build); publish is separate/manual
```

## Corporate / air-gapped install

CloakBrowser's binary download (~200MB) is the one thing that needs internet
access beyond PyPI, and it's the one most likely to be blocked by a
TLS-inspecting corporate proxy (e.g. Netskope) or an air-gapped policy. Three
options, pick whichever fits:

1. **Internal mirror** — point `CLOAKBROWSER_DOWNLOAD_URL` at a host you
   control that mirrors the binary; also set `HTTPS_PROXY`/`HTTP_PROXY` and
   `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` (your corporate root CA) if the
   gateway does TLS inspection.
2. **Pre-staged binary** — copy a CloakBrowser binary to the target machine
   yourself and set `CLOAKBROWSER_BINARY_PATH` to it; `webllm-proxy install`
   then skips the download entirely.
3. **Offline bundle** — on a machine *with* internet access:

   ```bash
   uv run poe bundle    # -> dist/offline/
   ```

   This collects this package's own wheel + every dependency wheel (via `uv
   build` + `uv export --no-emit-project` + `pip download`), archives the
   already-installed CloakBrowser binary (run `webllm-proxy install` first),
   and writes `install_offline.sh` / `install_offline.ps1`. Copy the whole
   `dist/offline/` directory to the target machine and run the install
   script for your OS — it does `pip install --no-index --find-links wheels
   webllm-proxy` and extracts the CloakBrowser archive into the same cache
   dir (`~/.cloakbrowser/...`) CloakBrowser's own lookup already checks, so
   no further env var is needed.

`webllm-proxy install` prints this same guidance if a download fails, rather
than dying silently. A Docker fallback image, `cloakhq/cloakbrowser`, also
exists if none of the above fit your environment.

## Docs

`docs/discovery/` documents the reverse-engineering of each backend (the ChatGPT
web API + anti-bot flow, the Databricks llmproxy channel + model enumeration),
including the process, not just the result.
