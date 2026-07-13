# Open model-discovery tasks (post-rebuild)

Date: 2026-07-13
Tags: [databricks] [copilot]

The architecture rebuild (5-folder, 2-method provider protocol, unified
OpenAI-only server) wired **best-effort** automatic model listing for every
provider, with a safe fallback to the configured list. Two live-session
discovery tasks remain to make listing fully automatic (no hardcoded fallback):

## 1. Databricks `graphql/ConversationModelStatuses`  [databricks] — RESOLVED 2026-07-13

**Done** — see `2026-07-13-databricks-model-discovery.md`. Pinned the exact query,
found the operation is **server-safelisted** (`x-databricks-operation-identifier`
signs operationName+query+**variables**, so it must be replayed **verbatim** — a
trimmed/reformatted request 400s "operation not authentic"), stored it as
`providers/databricks/model_discovery.json`, and made `models()` fully live: it
filters the response in-page to `editor-assistant-agent-mode` and lists **every**
AVAILABLE name verbatim as `databricks__<name>` (no family mapping/flags/filtering;
`gpt*`→Azure / else→Anthropic routing is completions-only). The response **does**
carry the `gpt-*` Azure deployments alongside Claude, so both channels come from
this one call. Static `models`/`openai_models` config removed. Probe
(`scripts/dbx_models_probe.py`) confirms **10 of 13** listed complete (7 gpt-4o/4.1,
2 gpt-5-mini/nano, claude-4-5-sonnet; `*-combined` 500s, 2 are non-chat).

## 2. Copilot M365 RefreshNavPane manifest  [copilot]

`providers/copilot/__init__.py` currently ships a static tone/model list
(`_TONES` -> `copilot__<tone>`). To validate/replace it, capture a real
`POST m365.cloud.microsoft/chat {"action":"RefreshNavPane"}` response (the shell
capability manifest) and parse
`store.bizchatAsAgentGpt.clientPreferences.modelSelectorMetadata
.availableModelSelectionOptions`. We have the ChatHub WS HAR but not the manifest.
