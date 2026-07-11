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

## Update 1 (2026-07-10) — live browser validation + model enumeration

Probed a real logged-in session (headed login once → `~/.local/share/
databricks-proxy/profile`, then **headless reuse works** — `/auth/session/info`
returns userId with no re-login). All requests were issued **in-page** via
`page.evaluate` (`fetch(..., {credentials:'include'})`), so the httpOnly session
cookie auto-attaches and the CSRF token never leaves the browser. This is the
browser-backed pattern from the ChatGPT proxy, minus the composer typing —
here a plain in-page `fetch` replay works because there is no token-minting.

**Answers to the open questions:**

1. **Auth confirmed.** In-page `fetch` with `x-csrf-token` (from
   `/auth/session/info`) + `x-databricks-org-id` (the URL `?o=` value) + the
   session cookie is sufficient. No cookie extraction needed; keep the
   browser-backed model. `/auth/session/info` also gives `{accountId, sessionId,
   csrfToken, userId}`.
2. **Minimal body works.** A tiny `system` (`[{type:text,text:"test"}]`), **no
   tools**, `max_tokens:8` → `200`, real `claude-sonnet-4-5-20250929`. The 37 KB
   Genie system prompt and 30 built-in tools are **not required** — we can send
   our own.
3. **Client tools work.** Sending our own single `{type:"custom", name:"ping",
   input_schema:...}` tool → `200`, `stop_reason:"tool_use"`, a
   `content_block` `tool_use` named `ping`. Native tool calling with
   client-declared tools — **no emulation** (unlike ChatGPT).
4. `proxy/chat/completions` also routes through LLM Proxy and **requires a
   registered `clientId`** in `metadata.clientId` (an unregistered value →
   `400 BAD_REQUEST: clientId 'x' is not registered`). With `auto-rename-action`
   it returns `200` (the Azure body stringified under `completion`).

**Model registry (this account), by trial + error-message taxonomy:**

The proxy distinguishes three cases, which lets us map the registry without a
list endpoint:
- `200` → usable.
- `PERMISSION_DENIED: Model X is unavailable for clientId <cid>. Failed check:
  MEC. Error Code: MODEL_DISABLED` → **registered but entitlement-gated** for
  that client (the name is real).
- `NOT_FOUND: Model 'X' is not registered ... does not match any
  model_registration name or alias` → the name doesn't exist.

| Backend | Model registration | State |
|---|---|---|
| Bedrock/Anthropic (`llmproxy`, `endpoint: anthropic/v1/messages`) | **`claude-4-5-sonnet`** (alias `claude-sonnet-4-5`) → `claude-sonnet-4-5-20250929` | **ENABLED** ✅ |
| " | `claude-4-5-opus` (alias `claude-opus-4-5`), `claude-4-5-haiku`, `claude-4-sonnet`, `claude-3-7-sonnet`, `claude-4-1-opus`, `gemini-2-5-pro`, `llama-3-3-70b`, `llama-3-1-405b` | registered but **MODEL_DISABLED** for this client |
| Azure OpenAI (`proxy/chat/completions`) | **`gpt-41-mini-2025-04-14`**, **`gpt-41-2025-04-14`** | **ENABLED** ✅ |
| " | `gpt-4o`, `gpt-4o-mini`, `gpt-5`, `o3-mini`, ... | NOT_FOUND (not registered) |

So on this login the usable set is **Claude Sonnet 4.5** (rich: native tools +
interleaved thinking, via `llmproxy`) plus two **GPT-4.1** Azure deployments
(via `proxy/chat/completions`). Model **aliases** exist (`claude-sonnet-4-5` ==
`claude-4-5-sonnet`).

**Loophole noted (not pursued):** entitlement (`MEC`) is checked **per
`clientId`**. The two app clientIds seen (`editor-assistant-agent-mode`,
`auto-rename-action`) both deny the gated models, but other Databricks feature
clientIds (Genie spaces, dashboards, SQL assistant, ...) may carry different
entitlements — a possible way to reach the disabled Claude/Gemini/Llama models.
This edges into entitlement circumvention; leaving it as an observation pending
a decision.

**Build implications:** target `llmproxy` with `model_registration:
claude-4-5-sonnet`, a minimal `system`, and the client's own `tools`; expose it
to `pi` as an `anthropic-messages` provider (pi speaks Anthropic natively — no
OpenAI↔Anthropic conversion). Keep a headless browser-backed session (login
once), issue the `llmproxy` fetch in-page, and stream the native Anthropic SSE
straight through. Also expose the two GPT-4.1 deployments via the
`chat/completions` path if an OpenAI-shaped option is wanted.

## Update 2 (2026-07-10) — built + validated as a `webllm-proxy` provider

Implemented as the `databricks` provider in the unified **`webllm-proxy`** tool
(`src/webllm_proxy/providers/databricks/`). It exposes **`POST /v1/messages`**
(Anthropic Messages) on port 5103 and is a near pass-through: wrap the request in
the `_llmproxy_fields` envelope (`model` → `model_registration`), issue the fetch
**in-page** over CDP (drain the body so `loadingFinished` fires), and stream the
native Anthropic SSE straight back. Run:

```bash
DATABRICKS_PROXY_URL="https://<workspace>.cloud.databricks.com/?o=<org>" \
  webllm-proxy login   --provider databricks     # once, headed
DATABRICKS_PROXY_URL="…same…" webllm-proxy serve --provider databricks
```

**Validated end-to-end** against Claude Sonnet 4.5: streaming text, **native
`tool_use`** (a client-declared `get_weather` tool → `stop_reason:"tool_use"` +
`input_json_delta`), several sequential requests, **headless reuse** of the
one-time login, and graceful shutdown (0 orphan Chrome). The transport is solid;
tool calling is native (no emulation).

**Two gotchas fixed while wiring the server (the probe didn't hit them because it
issued all calls in one page context):**

1. **`system` is required.** A request with **no `system` block** gets a
   Databricks edge **`400` with an empty body** (not an llmproxy JSON error).
   The probe always sent a `system`; a bare `messages`-only request fails. The
   provider now injects a default `system` when the client sends none.
2. **Org id must be passed in, not read from the page.** The in-page fetch first
   read the org from `location.search` (`?o=`), but the **workspace SPA drops the
   query after it routes**, so the *second* request onward sent an empty
   `x-databricks-org-id` → the same empty-body `400`. Fixed by passing the org id
   from config (parsed from `DATABRICKS_PROXY_URL`) into the in-page fetch.

**Model scoping note:** on `client_id: editor-assistant-agent-mode` with a weak
`system`, the model deflects generic prompts ("I need to clarify the scope of
this assistant…") — the channel is scoped to the Databricks editor assistant. A
strong client system prompt (e.g. a coding-agent prompt from `pi`) overrides
this. Not a proxy issue; a product-level constraint of the channel.
