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

Requires Python >= 3.10 and [uv](https://docs.astral.sh/uv/). Pick whichever
fits:

```bash
# From a clone of this repo (development):
uv sync                     # create .venv, install deps + this package
uv run webllm-proxy install # pre-download the stealth browser (~200MB; optional)

# As a standalone CLI tool, from PyPI (once published):
uv tool install webllm-proxy

# As a standalone CLI tool, straight from this Git repo (no PyPI needed):
uv tool install --from git+https://github.com/SamuelHaidu/webllm-proxy webllm-proxy

# Fully offline (no PyPI, no browser download): download the zip for your OS
# from this repo's GitHub Releases page and see "Corporate / air-gapped
# install" below.
```

`uv tool install` puts a `webllm-proxy` executable on your `PATH` (run `uv
tool update-shell` once if it isn't already); `webllm-proxy install` still
needs to be run afterward to fetch the browser binary unless you used the
offline zip.

## Configure

`webllm-proxy.yaml` is gitignored (it can hold a personal `workspace_url`), so
copy the template first:

```bash
cp webllm-proxy.example.yaml webllm-proxy.yaml
```

Then set `enabled: true` on whichever provider(s) you actually have a web
login for and fill in any required fields:

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
    workspace_url: "https://<your-workspace>.cloud.databricks.com/?o=<org-id>"
  copilot:
    enabled: false
    edition: m365
```

Models are **not** listed in the config — each provider discovers its
available models live from the upstream web app on every `GET /v1/models`
call. `workspace_url` is the only field `databricks` requires; everything
else (`tokenizer`, `models.<slug>.tokenizer`, `system_prompt`, `user_suffix`,
`profile_dir`, `style_rules`, ...) is optional tuning — see the fully
commented `webllm-proxy.example.yaml` and
[Configuration reference](#configuration-reference) below.

## Run

Log in once per enabled provider (headed, needs a display — this ignores the
config's `headless` setting), then serve them all on one port:

```bash
uv run webllm-proxy login --provider chatgpt          # once, headed
uv run webllm-proxy serve --config-file ./webllm-proxy.yaml
```

```bash
curl -s http://127.0.0.1:5100/health        # {"status": "running", "providers": {...}}
curl -s http://127.0.0.1:5100/v1/models
curl -N http://127.0.0.1:5100/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"chatgpt__gpt-5-mini","stream":true,
       "messages":[{"role":"user","content":"Count to 5"}]}'
```

Research is a model, not a separate API: send `model: "chatgpt__research"` for a
long, web-search-backed, structured-markdown answer.

Running `webllm-proxy` with no subcommand is shorthand for `serve
--config-file ./webllm-proxy.yaml`. `webllm-proxy --version` and
`webllm-proxy <cmd> --help` also work.

## Use with the OpenAI SDK / `pi`

Any OpenAI-compatible client points at `http://127.0.0.1:5100/v1` with a
namespaced model id. `pi` consumes the OpenAI format natively — add one provider
in `~/.pi/agent/models.json` pointing at the unified endpoint and list the
`<provider>__<slug>` ids you want. (There used to be a dedicated
`@webllm-proxy/pi` extension package with extra tooling on top of plain model
access; it was retired when the server moved from a multi-port/gateway layout
to this single-process one — see `docs/pi/webllm-integration.md` for what it
did and how to rebuild it against the current server.)

## Configuration reference

The YAML config is the source of truth (parsed with pyyaml, validated with
pydantic — see `webllm_proxy/utils/config.py`). Fields per provider, all
optional except where noted:

| Field | Default | Meaning |
|---|---|---|
| `enabled` | `false` | must be `true` for the provider to boot at `serve`/`login` |
| `headless` | `true` | used by `serve`; `login` always runs headed regardless |
| `profile_dir` | per-OS data dir | override where the persistent browser profile lives |
| `tokenizer` | `openai/gpt-5` | BPE profile used to *estimate* `usage` (see below) |
| `models.<slug>.tokenizer` | — | per-model tokenizer override, e.g. for a mini/nano tier |
| `system_prompt` | none | name of a `prompts/system_prompts/<name>.md` file to send |
| `models.<slug>.system_prompt` | — | per-model override of `system_prompt` |
| `user_suffix` | none | literal text appended to every turn's user message |
| `models.<slug>.user_suffix` | — | per-model override of `user_suffix` |
| `databricks.workspace_url` | `""` (**required** when enabled) | workspace URL incl. `?o=<org-id>` |
| `databricks.style_rules` | `true` | inject the style-rules addendum into Genie/Claude turns |
| `copilot.edition` | `m365` | Copilot edition to drive |
| `copilot.url` | provider default | override the nav URL for a non-default tenant |

A few debug env vars remain: `WEBLLM_PROXY_DUMP_SSE=<path>` (dump raw captured
SSE to a file), `WEBLLM_PROXY_DUMP_DIR=<dir>` (where redacted
`*_last_request.json` dumps land, defaults to the OS temp dir).

## Design & known limitations

- **Browser-backed**: a pure HTTP reimplementation isn't feasible for chatgpt
  (per-request Turnstile/PoW). databricks could be mostly server-side, but
  reuses the same transport for now.
- **Emulated function calling (chatgpt, copilot)** via a tag prompt contract —
  reliability is model-dependent. **databricks** Claude is native; there are no
  native-channel interception tricks anymore (chatgpt's internal tool messages
  are ignored).
- **Serialized**: one turn at a time per provider (single browser each) — a
  second concurrent request to the same provider waits on the first.
- **`usage` is estimated, not measured, for chatgpt/copilot**: none of the
  three web apps expose a real token-count API, so `prompt_tokens`/
  `completion_tokens` are computed locally with a vendored BPE tokenizer
  (`tiktoken`, plus a vendored Claude vocab) per the `tokenizer` config above.
  **databricks** usage is real, reported by the upstream channel itself.
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
uv run poe release    # check + build (uv build)
uv run poe publish    # uv publish -- manual fallback; CI does this on every version bump (see below)
```

The `openai` / `anthropic` SDKs are dev-only, used purely as validation clients
in `tests/smoke_openai_sdk.py` to prove SDK compatibility across tools, thinking,
effort, roles, and streaming — never in the runtime path.

### CI / releasing a new version

Three workflows under `.github/workflows/`:

- **`ci.yml`** — every pull request into `main` runs `uv run poe check`
  (fmt/lint/typecheck/test). Required to pass before merging.
- **`release.yml`** — every push to `main` (i.e. every merge) re-runs the
  quality gate, then checks whether `webllm_proxy/_version.py`'s
  `__version__` is already tagged. If it's a new version: builds the sdist +
  wheel, `uv publish`es to PyPI (authenticated via PyPI **Trusted
  Publishing**/OIDC — no stored token), tags the commit `vX.Y.Z`, and creates
  the GitHub Release for that tag. A merge that doesn't bump `__version__` is
  a no-op here — nothing publishes until you do.
- **`offline-bundle.yml`** — triggered by the `vX.Y.Z` tag `release.yml`
  just pushed: builds the Linux + Windows offline bundles natively (one
  runner per OS) and attaches them as zips to that same GitHub Release.

To ship a release: bump `__version__` in `webllm_proxy/_version.py` in a PR,
merge it, and the rest is automatic.

One-time setup (can't be done from code): register this repo as a [Trusted
Publisher](https://docs.pypi.org/trusted-publishers/) for the `webllm-proxy`
project on PyPI, pointing at `release.yml` and the `pypi` environment it
runs under (Settings → Environments in this repo).

## Corporate / air-gapped install

CloakBrowser's binary download (~200MB) is the one thing needing internet beyond
PyPI, and the one most likely blocked by a TLS-inspecting corporate proxy or an
air-gapped policy.

**Simplest**: every GitHub Release ships a pre-built, fully offline zip for
Linux and Windows (`webllm-proxy-offline-linux-x64.zip` /
`-windows-x64.zip`, built by `offline-bundle.yml`) — download it from this
repo's Releases page, unzip on the target (no-internet) machine, and run
`install_offline.sh` / `install_offline.ps1` inside it. That installs the
package (`pip install --no-index --find-links wheels webllm-proxy`) and
extracts the matching CloakBrowser binary; no PyPI, no browser download,
nothing else needed.

Otherwise, any one of:

1. **Pre-staged binary** — set `CLOAKBROWSER_BINARY_PATH`; `webllm-proxy install`
   then skips the download.
2. **Internal mirror** — point `CLOAKBROWSER_DOWNLOAD_URL` at a mirror; also set
   `HTTPS_PROXY`/`HTTP_PROXY` and `REQUESTS_CA_BUNDLE`/`SSL_CERT_FILE` (your root
   CA) if the gateway does TLS inspection.
3. **Build your own offline bundle** — on a connected machine `uv run poe
   bundle` (or `bundle-linux` / `bundle-windows`) collects wheels + the
   CloakBrowser binary into `dist/offline/` with an install script
   (`install_offline.sh`/`.ps1`) for the target machine — useful if you need
   a build newer than the last tagged release, or a platform the Release
   zips don't cover.
4. **Docker fallback** — run the `cloakhq/cloakbrowser` image instead of a
   locally installed binary.

If external clients (`pi`, `curl`, the SDKs) run behind a proxy, keep local
traffic direct: `export NO_PROXY=127.0.0.1,localhost`.

## Docs

- `docs/discovery/` (start at its `README.md` index) documents the
  reverse-engineering of each backend — the ChatGPT web API + anti-bot flow,
  the Databricks llmproxy channel + model enumeration, the Copilot ChatHub
  protocol — including the process, not just the result.
- `docs/pi/` covers integrating this proxy with the `pi` coding agent: the
  general `pi` extension/SDK mechanics (`pi-extension-sdk-index.md`) and the
  retired dedicated `webllm` `pi` package (`webllm-integration.md`).

