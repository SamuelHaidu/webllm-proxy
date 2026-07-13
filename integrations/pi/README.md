# @webllm-proxy/pi

A [pi](https://pi.dev) package that surfaces **webllm-proxy** inside the `pi`
coding agent. Phase 0 registers a single `webllm` provider whose models are
auto-discovered from the proxy's **aggregator gateway**; later phases add agent
tools (research, memory, subagent), pass-through agents (`/chatgpt`, `/genie`),
and token-efficiency features (TOON, stored procedures, LSP).

> Security: pi packages run with full system permissions. Review before installing.

## Prerequisites

1. Run one or more provider proxies (each needs a one-time login):
   ```
   webllm-proxy serve --provider chatgpt      # :5102
   webllm-proxy serve --provider databricks   # :5103
   webllm-proxy serve --provider copilot       # :5104
   ```
2. Run the aggregator gateway (one OpenAI/Anthropic surface over all of them):
   ```
   webllm-proxy gateway                         # :5100
   ```
   It merges each running proxy's `/v1/models` (ids namespaced
   `<provider>__<slug>`) and routes requests by that prefix.

## Install into pi

```
pi install -l /abs/path/to/integrations/pi     # project-local (.pi/settings.json)
# or try without installing:
pi -e /abs/path/to/integrations/pi/extensions/webllm-provider.ts
```

Then:

```
pi --list-models | grep webllm
pi --provider webllm --model 'webllm/chatgpt__gpt-5' -p "hello"
```

Override the gateway location with `WEBLLM_GATEWAY_URL` (default
`http://127.0.0.1:5100`).

> Start order matters: the `webllm` provider discovers its models at pi
> **startup**. Bring up the proxy(ies) and the gateway *first*, then launch pi.
> If the gateway is down when pi starts, no `webllm` models register and
> `/chatgpt`/`/genie` report "no models found".

## Pass-through commands

Both switch the current session to a `webllm` model, differing only in local-tool
policy (they take an optional `[model-id]` and an optional inline `[message]`):

- **`/chatgpt [model-id] [message]`** — plain chatgpt.com web chat. Disables
  **all** local tools; nothing local can run against a web chat.
- **`/genie [model-id] [message]`** — databricks Claude as a working agent.
  **Keeps** pi's local tools active so it can actually do the work — start SQL
  warehouses / run queries via the `databricks` CLI under bash, read/write files,
  etc. The raw `llmproxy` channel runs no server-side tools of its own (unlike
  the Genie UI), so without local tools the model can only describe steps.

Tip: run `/genie` inside a real Databricks project directory rather than this
proxy's own repo, so the repo's `CLAUDE.md` (which describes the proxy) doesn't
confuse the model about where it is.

## ChatGPT emulated agent mode

chatgpt.com has no tool-calling API, so by default a `webllm/chatgpt__*` model is
plain chat. Turn on **chatgpt emulated agent mode** to also get chatgpt as a
first-class **coding agent**, native to pi: its prose renders as a normal
assistant message and its actions render/execute as normal tool calls
(read/write/bash/edit/find/grep) — pi runs the loop, approvals and all. Under the
hood a small prompt contract (`prompts/chatgpt_agent.md`) makes the model emit
one tagged action per turn, and the `webllm-agent` provider translates that to
native pi tool calls (`src/agentProtocol.ts` + `src/agentStream.ts`).

It's **off by default and additive** (the plain `webllm` provider is untouched).
Turn it on/off with the **`/webllm-agent`** command — it saves the setting and
applies immediately (no relaunch):

```
/webllm-agent on       # or: off, or bare /webllm-agent for status
```

That persists `{ "webllm": { "chatgptAgentMode": true } }` to
`~/.pi/agent/settings.json`. You can also edit that file (or `.pi/settings.json`
per-project) by hand, or set `WEBLLM_CHATGPT_AGENT=1` to override for one run.
(pi has no API for extensions to add a row to its built-in `/settings` screen,
so the toggle is this command rather than a `/settings` entry.)

When on, the chatgpt models also appear as `webllm-agent/chatgpt__<slug>`
("… (agent)"); pick one and use pi normally:

```
pi --model 'webllm-agent/chatgpt__gpt-5' -p "read main.py and add unittest tests"
```

> Same start-order rule as the provider: bring up the gateway + `--provider
> chatgpt` proxy first, then launch pi (models are discovered at startup).

## Develop

```
npm install
npm run check      # biome lint + tsc typecheck + vitest
```

- `src/` — pure, pi-free logic (model mapping, gateway client); unit-tested.
- `extensions/` — the pi extension entrypoints (`webllm-provider.ts`).

Pi core packages (`@earendil-works/pi-*`, `typebox`) are peer deps — provided by
the pi runtime, not bundled.
