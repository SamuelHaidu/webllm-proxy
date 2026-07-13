# The `integrations/pi/` package (retired) — what it was, how to rebuild it

The working TypeScript extension package that used to live at
`integrations/pi/` was removed as part of the single-server rebuild (it
targeted the old per-provider-port + aggregator-`gateway` architecture, which no
longer exists — see below). This doc preserves what it did and how it was
built, so it can be rebuilt against the current server without starting from
zero. The literal retired source is still recoverable from git history (see
the bottom of this file); this is the design reference, not a changelog.

For the pi extension/SDK mechanics themselves (provider registration, tools,
commands, config dirs), see `docs/pi/pi-extension-sdk-index.md` — that
reference is independent of this integration and still current.

## What it did

A `@webllm-proxy/pi` package registering a `webllm` provider inside the `pi`
coding agent, so any model this proxy bridges could be used as a normal pi
model (`pi --model 'webllm/chatgpt__gpt-5' -p "..."`), plus three extras built
on top of the plain provider:

1. **Model discovery** (`src/gateway.ts` + `src/models.ts`) — at pi startup,
   fetch the merged, namespaced `/v1/models` list (`<provider>__<slug>` ids,
   optional `_title`/`_max_tokens` hints passed through) and register one pi
   model config per entry. Pure mapping logic in `models.ts` (no pi import, unit
   tested standalone); the fetch itself in `gateway.ts`. Degrades gracefully to
   zero models on any fetch failure rather than crashing pi's startup.

2. **Pass-through commands** `/chatgpt [model-id] [message]` and `/genie
   [model-id] [message]` (`extensions/passthrough.ts` + pure logic in
   `src/passthrough.ts`) — switch the current pi session to a `webllm` model
   with a specific **local-tool policy**: `/chatgpt` disables every local tool
   (a plain web chat has nothing to run them against), `/genie` keeps them on
   (Databricks Claude as a working agent that can shell out to e.g. the
   `databricks` CLI, read/write files, etc., since the raw `llmproxy` channel
   runs no server-side tools of its own).

3. **ChatGPT emulated agent mode** (`extensions/chatgpt-agent.ts` +
   `src/agentProtocol.ts` + `src/agentStream.ts` + `src/agentTags.ts` +
   `src/agentSettings.ts`, prompt `prompts/chatgpt_agent.md`) — an
   opt-in (`/webllm-agent on`), additive `webllm-agent` custom provider that
   makes a `webllm/chatgpt__*` model behave as a first-class pi **coding
   agent**: pi runs the actual loop (approvals and all), the model's prose
   renders as a normal assistant message, and its one tagged action per turn
   becomes a real, native pi tool call (`read`/`write`/`edit`/`bash`/`find`/
   `grep`) via a small tag protocol. `src/agentTags.ts` (pure tag parsing,
   ported from and validated by `scripts/agent_prompt_probe.py` against a model
   that refused the older, more absolute `tool_contract.md` wording — see
   `docs/discovery/2026-07-13-copilot-live-test.md` for the same
   "refuses an absolute tool-exclusivity claim" behavior showing up again on
   Copilot) and `src/agentProtocol.ts` (pure glue, no pi/SDK value imports) are
   unit-testable standalone; `src/agentStream.ts` is the actual `streamSimple`
   plumbing, validated live rather than unit-tested. Toggle state persists to
   `~/.pi/agent/settings.json` / project `.pi/settings.json` as
   `{"webllm": {"chatgptAgentMode": true}}`.

4. Two smaller tool extensions: `extensions/memory.ts` (thin wrapper around the
   `ai-memory` CLI, `src/memoryClient.ts` — spawns the CLI directly via
   `execFile`, no shell, no MCP server involved) and `extensions/research.ts` +
   `extensions/subagent.ts` (`src/researchClient.ts` polls the proxy's async
   deep-research job API; `src/codeIndex.ts` is the pure prompt/answer-
   extraction logic for a nested read-only `code_index` subagent).

Package layout convention worth keeping: `src/` is pure, pi-SDK-free logic
(unit-testable with plain vitest), `extensions/` is the thin pi entrypoints
that import `src/` and call into the real `@earendil-works/pi-coding-agent`
SDK. `check` = biome lint + tsc typecheck + vitest.

## What changed, and what a rebuild needs to account for

The integration was built against the **aggregator-gateway** architecture:
each provider ran as its own `webllm-proxy serve --provider <name>` process on
its own port (chatgpt :5102, databricks :5103, copilot :5104), and a separate
`webllm-proxy gateway` process (:5100) merged their `/v1/models` and routed
`/v1/chat/completions` by the `<provider>__` prefix. `src/gateway.ts`'s
`WEBLLM_GATEWAY_URL` (default `http://127.0.0.1:5100`) pointed at that
aggregator.

That's gone. The current server is **one process, one port**: `webllm-proxy
serve --config-file webllm-proxy.yaml` boots every `enabled: true` provider
from one YAML file and serves the same merged `/v1/models` +
`/v1/chat/completions` surface directly — no separate gateway step. A rebuilt
`src/gateway.ts` would point straight at that server's base URL (still
overridable by an env var); the id namespacing (`<provider>__<slug>`) and the
overall "merged models, route by prefix" contract are unchanged, so `models.ts`
/ `passthrough.ts`'s pure logic should port with little more than the base-URL
default updated. The three providers are `chatgpt`/`databricks`/`copilot`
(copilot is new since this integration was last touched — see
`webllm_proxy/providers/copilot/`).

## Recovering the literal retired source

The last commit before this doc removed `integrations/pi/` wholesale (superseded
by this note): find it with

    git log --oneline --diff-filter=D -- integrations/pi | tail -1

then `git show <that-commit>^:integrations/pi/<path>` for any specific file, or
`git checkout <that-commit>^ -- integrations/pi` to restore the whole directory
into the working tree as a starting point.
