# 2026-07-12 — Genie Code as a real agent (prompt + 30 tools + orchestration)

**Goal (user steer):** reproduce the browser **Genie Code** experience in the
terminal via pi — same system prompt, same tools, tools executed **remotely** on
Databricks — with **pi as a thin client** (send message, get answer; no local
tools). This documents the reverse-engineering needed to build it.

**Source:** the real Genie Code HAR
`…/cognitive_prosthetic/docs/databricks_har/genie_code_call_llmproxy_<ws>.har`
(gitignored, in a sibling project). Explored with `scripts/har_explore.py`. All
samples here are sanitized.

## Architecture: client-orchestrated loop, per-tool remote execution

Genie Code is **not** a single server-side agent endpoint. The browser drives the
loop; `llmproxy` is **model-only** (Anthropic passthrough — emits `tool_use`,
never executes it). Per captured turn the browser:

1. `POST /ajax-api/2.0/conversation/llmproxy/` (system + tools + messages) → Claude
   returns `thinking`/`text`/`tool_use` (stop_reason `tool_use`).
2. **Executes that tool remotely** by calling the matching Databricks endpoint
   with the same session cookies + `x-csrf-token` + org id, e.g.
   `docSearch` → `POST /graphql/DocsSearch__deduped`.
3. Feeds the result back as a `tool_result` in the next `llmproxy` turn. Repeat
   until `end_turn`. (The HAR shows 4 `llmproxy` turns for one user message.)

**Design that matches the user's ask:** move that orchestration into the
**proxy** (it already holds the authenticated Databricks session via
CloakBrowser, and already does in-page `fetch` to `llmproxy`). The proxy injects
the real prompt + real tool schemas, runs the loop, executes each `tool_use`
against the matching Databricks endpoint (in-page fetch), and exposes ONE plain
chat turn. pi then talks to it as a **thin client with `setActiveTools([])`** —
"pi only sends and gets messages," tools run remotely.

## The real payload (llmproxy request #36)

- **System prompt: 36,910 chars** — opens `"You are Genie Code, specialized in
  helping users with Databricks products…"`. Stored **sanitized** (the user's
  email → `<USER_EMAIL>`; no workspace URL / token / org id present) at
  `webllm_proxy/prompts/genie_code_system.md`.
- **30 tools** (`type/name/description/input_schema`). Full inventory, grouped:
  - **code/data exec (remote compute):** `executeCode` (notebook/cluster),
    `runDatabricksCli`, `executeLakebasePostgresSql`, `renderChart`
  - **data discovery (read-only):** `readTable`, `tableSearch`, `querySearch`,
    `searchAssets`, `forYouAssets`, `findReferencesTool`, `checkPermissions`
  - **genie spaces:** `askGenieSpace`, `recommendDataroom`
  - **assets/files:** `editAsset`, `createAsset`, `openAsset`, `readAssetById`,
    `processDataFile`, `readSkillFile`, `fetchOmittedContent`
  - **docs/git/planning:** `docSearch`, `runGit`, `manageTodoList`
  - **spark api docs:** `spark_get_service_info`, `spark_search_apis`,
    `spark_list_apis`, `spark_get_api_info`, `spark_get_version_changes`,
    `spark_get_best_practices`, `spark_get_migration_analysis`

## Scope for the first build: read-only data tools

Chosen (no notebook/cluster lifecycle): `docSearch`, `tableSearch`,
`querySearch`, `readTable`, `searchAssets`, `askGenieSpace`, `manageTodoList`.
Their exact schemas are stored at `webllm_proxy/prompts/genie_code_tools.json`.

### Reverse-engineering status (critical)

This HAR only **exercised `docSearch`** (the one user message asked a docs
question). So only `docSearch` is fully spec'd; the other five remote tools need
a **fresh capture that triggers them** (drive Genie Code with, e.g.: "what tables
do I have about X" → `tableSearch`; "describe table <cat.sch.tbl>" → `readTable`;
"find a query that …" → `querySearch`; "search my dashboards/notebooks for …" →
`searchAssets`; "ask the <name> genie space …" → `askGenieSpace`).
`manageTodoList` is **local agent state** (no endpoint) — implement directly.

### `docSearch` executor (the template for all remote tools)

- **Request:** `POST /graphql/DocsSearch__deduped`
  ```json
  {"operationName":"DocsSearch__deduped",
   "variables":{"input":{"query":"<searchQuery>","docVersion":"PUBLIC_DOCS",
                         "size":5,"minimumScore":0.689}},
   "query":"query DocsSearch__deduped($input: SearchmidtierSearchDocsInput!) …"}
  ```
- **Response:** `data.searchmidtierDocsSearch.results[]` → `{url, content}`
  (plus `apiError{code,message}`). Map `results` into the `tool_result` text.

## Build order (proposed)

1. Proxy-side **orchestrator**: inject `genie_code_system` + the read-only
   `genie_code_tools`, run the `llmproxy` loop, dispatch `tool_use` → executor,
   loop to `end_turn`. Expose as a chat turn (reuse the Anthropic surface or a
   new `/v1/genie` route).
2. Executors buildable **now**: `docSearch` (above) + `manageTodoList` (local).
3. Executors needing a **fresh capture** first: `tableSearch`, `querySearch`,
   `readTable`, `searchAssets`, `askGenieSpace`.
4. pi `/genie` → thin-client mode against the orchestrator (`setActiveTools([])`),
   replacing the current "give pi its own tools" behavior once the loop works.

## Open questions / risks

- **Auth for non-llmproxy endpoints:** the in-page fetch must send the right
  `x-csrf-token`/org id for `/graphql/*` and `/ajax-api/*` (llmproxy already
  works; assume same, verify per endpoint).
- **`executeCode` deferred** (out of this scope): needs notebook create + cluster
  attach + command submit/poll (`POST /notebook`, `/notebook/{id}/command/{cmd}`,
  GET `/notebook/{id}/command`). Biggest single piece; revisit after read-only.
- **Proprietary content:** `genie_code_system.md` is Databricks' verbatim prompt
  (sanitized). Committed because the feature needs it and the user asked to keep
  the original; trivially gitignored instead if preferred.
