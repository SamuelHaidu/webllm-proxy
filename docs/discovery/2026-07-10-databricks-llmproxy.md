# Databricks Genie / Assistant `llmproxy` — HAR capture analysis (2026-07-10)

**New target, separate from ChatGPT.** Same project goal (expose a
login-only LLM as an OpenAI-compatible local API), applied to a Databricks
workspace's in-product "Genie code" / editor assistant. This entry is the
**pre-browser** analysis: it was derived entirely from a captured HAR so that
the later browser session knows exactly what to look for.

## Source & tooling

- Capture: `docs/databricks_har/genie_code_call_llmproxy_<workspace>.har`
  (6.5 MB, **gitignored** — contains live session context; never commit).
- Explorer: `scripts/har_explore.py` — parses the whole HAR in-process but only
  prints compact, **secret-redacted** summaries (never dump a HAR into an LLM
  context). Subcommands: `paths`, `list`, `show N`, `req/resp N`, `headers N`,
  `keys N`, `grep`. Redacts `Authorization`/`Cookie`/`*token*` headers and
  credential-looking JSON keys as `<redacted len=N>` while keeping structure.
  - `python scripts/har_explore.py FILE paths`
  - `python scripts/har_explore.py FILE keys 36 req`  (JSON key-tree of a body)

## Endpoint map (73 entries)

Two LLM channels, on the same host, same cookie-based auth:

| Channel | Endpoint | Backend | Wire format |
|---|---|---|---|
| **A (target)** | `POST /ajax-api/2.0/conversation/llmproxy/` | **Anthropic on AWS Bedrock** | **native Anthropic Messages API** + SSE |
| B | `POST /ajax-api/2.0/conversation/proxy/chat/completions` | **Azure OpenAI** | OpenAI chat.completions (wrapped) |

Supporting endpoints seen: `/auth/session/info`, `/auth/session/refresh`,
`/graphql/{ConversationModelStatus,CustomCodeAgentsEndpoints,UnifiedSearchGlobalAssistant}`,
`/api/2.0/popproxy/health`, notebook/workspace-file CRUD.

## Channel A — `llmproxy` (the Genie-code channel; **use this**)

### Request body = Anthropic Messages API + a routing envelope

```jsonc
{
  "messages": [ { "role": "user", "content": "..." }, ... ],   // Anthropic messages
  "system":   [ { "type": "text", "text": "<~37KB agent prompt>",
                  "cache_control": { "type": "ephemeral" } } ], // prompt caching
  "tools":    [ { "type": "...", "name": "docSearch",
                  "description": "...",
                  "input_schema": { "type":"object",
                     "properties": {...}, "required":[...],
                     "additionalProperties": false } }, ...30 tools ],
  "max_tokens": 64000,
  "stream": true,
  "thinking": { "type": "enabled", "budget_tokens": 16000 },   // extended thinking
  "anthropic_beta": [ "interleaved-thinking-2025-05-14" ],
  "_llmproxy_fields": {
    "model_registration": "claude-4-5-sonnet",   // <-- selects the model
    "endpoint": "anthropic/v1/messages",         // <-- selects the wire API
    "agent_name": "GenieCodeFullChat",
    "client_id": "editor-assistant-agent-mode",
    "session_id": "<redacted>",
    "trace_id": "<uuid>", "call_id": "<uuid>"
  }
}
```

- There is **no top-level `model`** — the model is chosen by
  `_llmproxy_fields.model_registration` (`claude-4-5-sonnet`). `endpoint`
  (`anthropic/v1/messages`) tells the proxy which upstream wire format the body
  is in. So `llmproxy` is a *multiplexer*: change these two fields and (likely)
  the body shape to route to a different model/provider. **To verify in the
  browser: what other `model_registration` values are accepted.**
- The 30 tools are Databricks agent tools (`executeCode`, `runDatabricksCli`,
  `runGit`, `readTable`, `askGenieSpace`, `createAsset`, `docSearch`, ...). For
  our OpenAI proxy we would replace these with the *client's* declared tools.

### Response = native Anthropic SSE (`text/event-stream`)

Event stream is standard Anthropic:
`message_start` → (`content_block_start`/`content_block_delta`/`content_block_stop`)×N → `message_delta` → `message_stop`.

```
event: message_start
data: {"type":"message_start","message":{"model":"claude-sonnet-4-5-20250929",
       "id":"msg_bdrk_...","role":"assistant","usage":{"input_tokens":1885,
       "cache_creation_input_tokens":9673,"cache_read_input_tokens":34483,...}}}
event: content_block_start
data: {...,"content_block":{"type":"thinking","thinking":"","signature":""}}
event: content_block_delta
data: {...,"delta":{"type":"thinking_delta","thinking":"..."}}   // reasoning
...                 "delta":{"type":"text_delta","text":"..."}   // visible text
...  content_block {"type":"tool_use","name":"docSearch",...} + input_json_delta
event: message_delta
data: {...,"delta":{"stop_reason":"tool_use"|"end_turn"}}
```

- **Native tool calling confirmed.** Across the 4 captured turns: blocks were
  `thinking`+`text`+`tool_use` (stop_reason `tool_use`, calling `docSearch`),
  and the final turn `thinking`+`text` (stop_reason `end_turn`). No emulation
  needed — contrast ChatGPT web, where there's no client-facing tool API and we
  had to inject a text contract + intercept the native recipient channel.
- Backend is AWS Bedrock (`x-amzn-bedrock-content-type: application/json`,
  ids `msg_bdrk_...`). Prompt caching is on (`cache_read_input_tokens`).

## Channel B — `proxy/chat/completions` (Azure OpenAI, already OpenAI-shaped)

```jsonc
// request
{ "params": { "messages":[...], "model":"gpt-41-mini-2025-04-14",
              "temperature":0.7, "stream":false },
  "metadata": { "traceId":"...", "clientId":"auto-rename-action" },
  "@method": "openAiServiceChatCompletionRequest",
  "deployment": "gpt-41-mini-2025-04-14", "model":"...", "apiVersion":"2025-01-01-preview" }
// response  (Azure OpenAI body, stringified under "completion")
{ "completion": "{\"choices\":[{...\"message\":{\"role\":\"assistant\",
                 \"content\":\"...\"}...}],\"model\":\"gpt-4.1-mini-2025-04-14\",
                 \"object\":\"chat.completion\",\"usage\":{...}}" }
```

- This is the *utility* model (used here for auto-rename). Simplest to proxy
  (unwrap the `completion` string → it's already an OpenAI `chat.completion`).
  **Open question:** does it accept `stream:true`, arbitrary `model`/
  `deployment`, tools? Likely a fixed small model. Channel A is richer.

## Auth (both channels) — **much simpler than ChatGPT**

Per-request headers (HTTP/2):

- `x-csrf-token` (36-char) — **also served in JSON by `GET /auth/session/info`**
  as `{secsSinceIssuedAt, accountId, sessionId, csrfToken, userId}`. So a proxy
  can fetch a fresh CSRF token from that endpoint given the session cookie.
- `x-databricks-org-id: <org-id>`
- `origin` / `referer` = the workspace URL; `content-type: application/json`;
  `accept: text/event-stream`.
- **Session cookie** = httpOnly, so it was **stripped from the HAR** (no
  `Cookie` request header, no `Set-Cookie`). Identifying the exact cookie
  name(s) is a **browser-phase task**.
- `POST /auth/session/refresh` — empty body; refreshes the session cookie
  (cookie-based). A proxy can call this to keep the session alive.

### No anti-bot challenge

A sweep of every request/response header for
`sentinel|turnstile|proof-of-work|captcha|challenge` → **NONE**. Unlike
chatgpt.com there is **no Cloudflare Turnstile and no proof-of-work**. Auth is
just *session cookie + CSRF token + org-id*, all obtainable once per session.

## Consequences — reusing ChatGPT lessons, and where Databricks is easier

1. **A pure server-side HTTP proxy is likely feasible** (no browser in the
   request loop). ChatGPT *required* a live browser to mint per-request
   Turnstile/PoW tokens; Databricks does not. The browser is only needed
   **once**, to obtain the session cookie (and thereafter `/auth/session/refresh`
   + `/auth/session/info` can be driven server-side).
   - The httpOnly cookie is the one hard part. Two paths (same tradeoff we hit
     with the Chrome profile): (a) run the `fetch` **inside the logged-in page**
     (`credentials:'include'`, cookie auto-attached) and capture the SSE — the
     browser-backed pattern we already built for ChatGPT; or (b) extract the
     cookie via CDP `Network.getAllCookies` / the profile cookie store and
     replay server-side. (b) is the big simplification Databricks enables.
2. **Tool calling is native Anthropic** → map OpenAI `tools`/`tool_calls`
   1:1 to Anthropic `tools`/`tool_use`/`tool_result`. No contract injection, no
   recipient interception. Much more reliable than the ChatGPT path.
3. **Reasoning** maps cleanly: OpenAI `reasoning_effort` → Anthropic
   `thinking.budget_tokens` (+ `anthropic_beta` interleaved-thinking); emit
   `thinking_delta` as reasoning content.
4. **Model routing** is via `_llmproxy_fields.model_registration` — enumerate
   accepted values in the browser (Claude family at least; possibly the
   Azure/OpenAI + foundation-model endpoints too).

## OpenAI-compat mapping (target: Channel A)

| OpenAI (`/v1/chat/completions`) | Databricks llmproxy (Anthropic) |
|---|---|
| `messages[]` (system/user/assistant/tool) | `system` block(s) + `messages[]` (Anthropic roles; `tool` → `tool_result`) |
| `model` | `_llmproxy_fields.model_registration` |
| `tools[].function` | `tools[]` (`name`/`description`/`input_schema`) |
| `tool_calls` (assistant) | `content_block` `tool_use` |
| `reasoning_effort` | `thinking.budget_tokens` |
| `stream` | `stream` (Anthropic SSE → OpenAI chunks) |
| assistant text | `text_delta`; reasoning ← `thinking_delta` |
| `finish_reason` `tool_calls`/`stop` | `stop_reason` `tool_use`/`end_turn` |
| `usage` | `message_start.usage` (+ cache tokens) |

## Open questions for the browser session (what to probe)

1. **Session cookie name(s)** and whether an in-page `fetch` (credentials
   included) is enough to replay `llmproxy` — decide browser-backed vs
   cookie-extraction.
2. **Accepted `model_registration` values** (and whether `endpoint` +
   `model_registration` can select the OpenAI/foundation-model backends too).
3. Whether the huge `system` prompt + all 30 tools are **required**, or the
   endpoint accepts a minimal `system`/`tools` (so we can inject our own).
4. `proxy/chat/completions`: does it stream / accept other models?
5. Rate limits / quotas (headers were clean here; watch under load).
6. CSRF-token lifetime and whether `/auth/session/refresh` alone keeps
   `llmproxy` authorized over time.
