# pi Extension + SDK — reference index

A findable map of `pi` (`@earendil-works/pi-coding-agent`) for building the
`integrations/pi/` package. Written 2026-07-11 against **v0.80.6**. Re-derive
from the on-disk docs below if the version changes.

> `pi` is the installed coding agent (`which pi` →
> `~/.nvm/.../bin/pi`, package `@earendil-works/pi-coding-agent`). It has a
> provider / tool / command / hook extension model **and** a programmatic SDK.

## On disk

- Package root: `~/.nvm/versions/node/v24.11.1/lib/node_modules/@earendil-works/pi-coding-agent/`
  - `dist/index.d.ts` (types entry), `dist/cli.js` (the `pi` bin)
  - `docs/` (30 files — see map), `examples/{extensions,sdk}/`
  - `README.md`, `CHANGELOG.md`
- Import roots (peer-dep with `"*"` in a package; do not bundle):
  - `@earendil-works/pi-coding-agent` — `ExtensionAPI`, SDK, events, `defineTool`
  - `@earendil-works/pi-ai` — provider stream types, `StringEnum`, `calculateCost`
  - `@earendil-works/pi-tui` — TUI components (custom rendering)
  - `typebox` — tool/param schemas (`Type.Object({...})`)
- jiti loads `.ts` extensions directly (no build step required).

## Config / data dirs

- Global: `~/.pi/agent/` — `settings.json`, `auth.json` (OAuth/API keys),
  `models.json` (custom models), `sessions/` (JSONL), `extensions/`, `npm/`,
  `bin/rg`.
- Project: `.pi/` — `extensions/`, `skills/`, `prompts/`, `settings.json`,
  `npm/`, `git/`. Loaded only after the project is trusted.
- Never hardcode `.pi`; import `CONFIG_DIR_NAME` from the package.

## Docs map (`docs/<file>.md`)

| File | Covers |
|------|--------|
| `extensions.md` | events/hooks, `registerTool`/`registerCommand`, `ExtensionContext`, custom UI |
| `custom-provider.md` | `registerProvider`, model config, `streamSimple`, OAuth, overflow |
| `providers.md` | provider concepts / built-ins |
| `sdk.md` | `createAgentSession`, run modes, `SessionManager`, `defineTool` |
| `packages.md` | packaging (npm/git/local), `pi` manifest, filtering |
| `skills.md`, `prompt-templates.md` | skills + `/`-command templates |
| `models.md`, `settings.md` | model selection, settings schema |
| `sessions.md`, `session-format.md` | session tree, JSONL entry types (for analysis) |
| `rpc.md`, `json.md` | RPC/JSON run-mode protocols |
| `compaction.md`, `themes.md`, `keybindings.md`, `tui.md` | misc runtime |
| `security.md`, `containerization.md` | trust/sandboxing |

## Extension shape

```ts
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
export default function (pi: ExtensionAPI) { /* ... */ }   // async factory OK; awaited before startup
```

**`ExtensionAPI` methods:** `on(event,h)`, `registerTool`, `registerCommand`,
`registerShortcut`, `registerFlag`, `registerProvider` / `unregisterProvider`,
`sendMessage` / `sendUserMessage`, `setActiveTools` / `getAllTools`,
`appendEntry` / `registerEntryRenderer`, `pi.events` (event bus).

**Hooks worth targeting for this project:**
- `before_agent_start` — mutate `systemPrompt`, inject a message (persona/guidelines)
- `context` — mutate `messages` before each LLM call
- `tool_call` — block or mutate tool input
- `tool_result` — rewrite a tool result (TOON encoding hook)
- `resources_discover` — contribute skill/prompt/theme paths
- `before_provider_request` / `before_provider_headers` / `after_provider_response`
- `session_start` / `session_shutdown`, `model_select`

**`ExtensionContext`:** `ctx.ui` (`select`/`confirm`/`input`/`notify`/`setStatus`/
`setWidget`/`custom`), `ctx.mode`, `ctx.hasUI`, `ctx.cwd`, `ctx.sessionManager`,
`ctx.modelRegistry`/`ctx.model`, `ctx.signal`, `ctx.isIdle()`/`abort()`,
`ctx.getSystemPrompt()`, `ctx.getContextUsage()`, `ctx.compact()`. Command ctx
adds `newSession`/`fork`/`switchSession`/`navigateTree`/`reload`/`waitForIdle`.

## registerProvider (our model discovery path)

```ts
pi.registerProvider("webllm", {
  name: "WebLLM Proxy",
  baseUrl: "http://127.0.0.1:5100/v1",     // the aggregator gateway
  apiKey: "$WEBLLM_API_KEY",                // env / !cmd / literal; optional
  api: "openai-completions",                // | "anthropic-messages" | "openai-responses" | ...
  models: [{ id, name, reasoning, input:["text"], cost:{input,output,cacheRead,cacheWrite},
             contextWindow, maxTokens, thinkingLevelMap?, compat? }],
});
```

An **async factory** may `fetch(baseUrl + "/models")` and register the mapped
models before startup (they then show in `pi --list-models`). `models` replaces
all models for that provider. `compat`/`thinkingLevelMap` tune quirks.

## registerTool

```ts
import { Type } from "typebox";
pi.registerTool({
  name: "my_tool", label: "My Tool", description: "...",
  promptSnippet: "one-line for Available tools", promptGuidelines: ["Use my_tool when ..."],
  parameters: Type.Object({ q: Type.String() }),
  async execute(toolCallId, params, signal, onUpdate, ctx) {
    return { content: [{ type: "text", text: "..." }], details: {} };
  },
});
```

Registrable at load or runtime (`pi.setActiveTools` to toggle).

## SDK (subagents / nested runs)

```ts
import { createAgentSession, runPrintMode, defineTool, SessionManager } from "@earendil-works/pi-coding-agent";
const { session } = await createAgentSession({ model, tools:["read","grep","find"], customTools, noTools, resourceLoader });
await session.prompt("..."); session.subscribe(ev => {/* text_delta, tool_* */});
```

Other exports: `runRpcMode`, `InteractiveMode`, `SettingsManager`, `AuthStorage`,
`ModelRegistry`, `resolveCliModel`, `DefaultResourceLoader`
(`systemPromptOverride`/`extensionFactories`/`agentsFilesOverride`/
`additionalExtensionPaths`), tool factories (`createReadOnlyTools`, …),
`getAgentDir`/`getDocsPath`, `CONFIG_DIR_NAME`.
`SessionManager.list/listAll/open` + tree API (`getEntries`/`getPath`/`branch`)
— used by the stored-procedures analyzer to mine `~/.pi/agent/sessions/*.jsonl`.

## Packaging (`docs/packages.md`)

`package.json` with a `pi` key + `pi-package` keyword:

```json
{ "name": "...", "keywords": ["pi-package"],
  "pi": { "extensions": ["extensions"], "skills": ["skills"], "prompts": ["prompts"] } }
```

Install: `pi install npm:… | git:… | ./path` (`-l` writes project settings
`.pi/settings.json`). Try without install: `pi -e ./path`. Enable/disable via
`pi config`.

## Directly-relevant examples (`examples/`)

- `extensions/subagent/` — spawn nested agents (model for our `subagent` tool)
- `extensions/custom-provider-anthropic/`, `custom-provider-gitlab-duo/` — provider + OAuth
- `extensions/structured-output.ts`, `dynamic-tools.ts`, `tool-override.ts` — tool patterns
- `extensions/summarize.ts`, `handoff.ts`, `commands.ts`, `send-user-message.ts`
- `extensions/dynamic-resources/`, `prompt-customizer.ts`, `system-prompt-header.ts`
- `sdk/05-tools.ts`, `06-extensions.ts`, `12-full-control.ts`, `13-session-runtime.ts`

## CLI flags relevant to integration

`--provider`, `--model` (`provider/id[:thinking]`), `--tools`/`--no-tools`/
`--exclude-tools`, `-e/--extension`, `--skill`, `--prompt-template`,
`--mode text|json|rpc`, `-p/--print`, `--list-models`, `--append-system-prompt`,
`--no-context-files`.

## How this repo plugs in

`integrations/pi/` (TypeScript pi-package) registers ONE `webllm` provider
against the aggregator gateway (`webllm-proxy gateway`, default `:5100`), which
merges each running per-provider proxy's `/v1/models` (namespaced
`<provider>__<slug>`) and routes `/v1/chat/completions` etc. See the roadmap in
the plan and `integrations/pi/README.md`.
