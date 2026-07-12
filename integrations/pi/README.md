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

## Develop

```
npm install
npm run check      # biome lint + tsc typecheck + vitest
```

- `src/` — pure, pi-free logic (model mapping, gateway client); unit-tested.
- `extensions/` — the pi extension entrypoints (`webllm-provider.ts`).

Pi core packages (`@earendil-works/pi-*`, `typebox`) are peer deps — provided by
the pi runtime, not bundled.
