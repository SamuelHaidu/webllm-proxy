# Databricks automatic model discovery (ConversationModelStatuses)

Date: 2026-07-13
Tags: [databricks]

Resolves task 1 of `2026-07-13-open-model-discovery-tasks.md`. The Databricks
model list is now **fully auto-discovered live** — the static `models` /
`openai_models` lists were removed from `webllm-proxy.yaml` and from the
provider code. Source HAR: `genie_code_call_completitions_llmproxy_model_discovey.har`.

## The endpoint

`POST /graphql/ConversationModelStatuses` returns per-model availability, grouped
by **clientId** (Databricks gates models per client via MEC entitlements). We
drive every request as `editor-assistant-agent-mode` (`llmproxy.CLIENT_ID`), so
we keep only the models AVAILABLE **for that clientId** — the exact set the
llmproxy channel accepts. Response shape (pinned):

    data.conversationListModelAvailability.modelAvailability[] = {
      clientId, modelStatuses[] = { isAvailable, name, status }
    }
    status ∈ { AVAILABLE, MODEL_DISABLED, MODEL_NOT_FOUND }

## The catch: the operation is server-safelisted (persisted query)

The request must carry `x-databricks-operation-identifier` — a 64-hex value that
is **not** a recomputable hash of the query (tested sha256 over query, query+vars,
raw body, normalized whitespace — none matched). It is a build-time
**persisted-operation signature** that covers `operationName` + `query` +
**variables**. Consequences, confirmed by a live variant sweep (`x-databricks-self:
true` + the captured op-id, byte-exact query):

- exact query + op-id + **full 36 clientIds**  → **200 OK**
- exact query + op-id + **single clientId**    → 400 `{"errors":[{"message":"Graphql operation not authentic"}]}`
- reformatted query (different whitespace)      → 400 "operation not authentic"
- no op-id / no `x-databricks-self`             → 400 "operation not authentic"

So the operation cannot be trimmed or reformatted: it must be **replayed
verbatim**. We pin the exact request (operationName / operationId / clientIds /
query) as an asset, `webllm_proxy/providers/databricks/model_discovery.json`, and
filter the response **in-page** to `editor-assistant-agent-mode` so only a small
slice crosses the CDP boundary. If Databricks ever changes the operation, the
call 400s "not authentic" and `models()` degrades to an empty list (logged) —
re-capture the request from a fresh HAR and overwrite the asset.

Headers that matter: `x-csrf-token` (from `/auth/session/info`),
`x-databricks-org-id`, `x-databricks-self: true`, `x-databricks-operation-identifier`.

## What's actually available on this login (agent-mode)

Live `ConversationModelStatuses` → 13 AVAILABLE for `editor-assistant-agent-mode`.
Then `scripts/dbx_models_probe.py` (default `discover` mode) sends a real 1-token
completion to each, on its channel, to check they *actually complete*:

| model                          | channel   | probe   |
|--------------------------------|-----------|---------|
| gpt-4o-2024-05-13              | azure     | WORKS   |
| gpt-4o-2024-08-06              | azure     | WORKS   |
| gpt-4o-hybrid                  | azure     | WORKS   |
| gpt-4o-mini-2024-07-18         | azure     | WORKS   |
| gpt-41-2025-04-14              | azure     | WORKS   |
| gpt-41-mini-2025-04-14         | azure     | WORKS   |
| gpt-41-nano-2025-04-14         | azure     | WORKS   |
| gpt-5-mini-2025-08-07          | azure     | WORKS   |
| gpt-5-nano-2025-08-07          | azure     | WORKS   |
| claude-4-5-sonnet              | anthropic | WORKS   |
| gpt-5-mini-2025-08-07-combined | azure     | **500** INTERNAL_ERROR (entitled but not servable) |
| text-embedding-3-large-1       | —         | skipped (embedding, not chat) |
| ghosttext-code-completion      | —         | skipped (code-completion, not chat) |

**10 usable chat models**, but `models()` lists **all 13** verbatim (see below). Notes:

- **gpt-5 rejects a non-default `temperature`** ("Unsupported value: 'temperature'").
  The provider's `build_azure_body` only forwards messages/model/stream (no forced
  temperature), so gpt-5* work through the proxy; a client that *sends* temperature
  to a gpt-5 model will 400. The probe was corrected to match the provider (no
  forced temperature) — with `temperature:0` the gpt-5* models falsely showed 400.
- **`gpt-5-mini-...-combined`** is entitled (AVAILABLE) but 500s on use — a
  composite/router registration, not directly servable.
- **`text-embedding-3-large-1`** and **`ghosttext-code-completion`** are non-chat;
  they error if called through chat completions.

## Listing: every discovered name, verbatim (no heuristics)

`models()` exposes **every** name discovered as AVAILABLE, as-is, namespaced
`databricks__<name>` — uniform entries (`id`/`object`/`created`/`owned_by`), no
family mapping, no capability flags, no filtering. Whether a model actually
completes (and the non-chat/combined caveats above) is what the **probe** reports;
listing itself is pure discovery.

Channel selection is a **completions-only** concern (ConversationModelStatuses
carries no channel/endpoint field): `_is_gpt_model` sends `gpt*` to the Azure
`proxy/chat/completions` channel and everything else to the Claude/Anthropic
Messages channel. That one prefix check routes requests; it is never used for the
model list.

## Code changes

- **Removed static model config**: `DatabricksConfig.models` / `.openai_models`
  (config.py), the `models:`/`openai_models:` lines in `webllm-proxy.yaml`, and
  the `claude_models`/`openai_models` constructor args + wiring
  (`providers/__init__.py`, `providers/databricks/__init__.py`).
- **`providers/databricks/model_discovery.json`** — the pinned verbatim request.
- **`providers/databricks/__init__.py`** — `MODELS_JS` replays the pinned request
  and filters in-page to the clientId; `models()` lists every discovered name
  verbatim (no family filter/flags); channel routing (`_is_gpt_model`) is
  completions-only; `_DEFAULT_MODEL` is the only literal (no-model safety net).
- **`providers/databricks/models.py`** — `parse_model_statuses(response,
  client_id)` rewritten for the exact shape (was a tolerant walker);
  `discovery_request()` loads the asset.
- **`scripts/dbx_models_probe.py`** — default `discover` mode: live-discovers via
  the same `MODELS_JS`/parser the provider uses, then probes each discovered model
  on its channel and prints the working set. Legacy explicit modes kept.
- **`tests/test_databricks.py` / `test_config.py`** updated to the new shape.

Validated live: `provider.models()` via the real build path returns all 13
discovered names, namespaced `databricks__<name>` (uniform entries:
id/object/created/owned_by), clean shutdown (0 orphan Chrome).

## Genie Code system prompt (refreshed)

The same HAR carries a newer **Genie Code** system prompt (37,977 chars, client
`editor-assistant-agent-mode`) than the stored copy. Saved sanitized (user email →
`<USER_EMAIL>`) to `webllm_proxy/prompts/system_prompts/databricks_genie_code_local_system_prompt.md`
(reference artifact; the provider itself layers the shorter
`databricks_default_system_prompt.md` framing).
