# 2026-07-10 — ChatGPT backend API capture (authenticated)

Captured by logging into a persistent CloakBrowser profile and sending one
real message while recording network traffic (script approach in
`2026-07-10-cloakbrowser.md`). Sanitized samples live in `samples/`
(all tokens/cookies redacted). Base host: `https://chatgpt.com`.

## TL;DR — the send path and its gate

The message-send endpoint is **`POST /backend-api/f/conversation`** (SSE
response). It is gated by an anti-bot **"sentinel"** flow and needs **three
per-request tokens** plus a Bearer JWT:

1. `authorization: Bearer <accessToken>` — from `GET /api/auth/session`.
2. `openai-sentinel-chat-requirements-token` — from the sentinel
   prepare→finalize flow (below).
3. `openai-sentinel-proof-token` — a **client-computed proof-of-work**.
4. `openai-sentinel-turnstile-token` — a **Cloudflare Turnstile token**
   (browser-generated, effectively single-use).

**Implication (critical):** #4 (and to a lesser degree #3) are produced by
running Cloudflare/OpenAI JS *inside a real browser*. A pure server-side HTTP
proxy cannot mint a Turnstile token without a browser or a paid solver. So
the proxy must keep an authenticated **CloakBrowser** session in the loop to
mint these tokens (see the architecture decision at the bottom).

## Auth: `GET /api/auth/session`

Returns JSON (see `samples/auth_session.redacted.json`). Key fields:

- `accessToken` — the Bearer JWT (~1888 chars) used as `Authorization`.
- `sessionToken` — a JWE; the durable session credential (the
  `__Secure-next-auth.session-token` cookie value). **Treat as a password**
  (the payload literally ships a "DO NOT SHARE" banner). Never log or persist.
- `user` {id, name, email, image, picture, idp, iat, mfa}, `account`,
  `expires` (months out), `authProvider`.

The persistent CloakBrowser profile already holds the cookies, so the browser
is authenticated on launch; `accessToken` is just re-read from this endpoint.

## Required client headers (seen on every backend-api call)

- `authorization: Bearer …` (auth calls only)
- `oai-device-id: <uuid>` (stable per profile)
- `oai-client-version: prod-<gitsha>` and `oai-client-build-number: <int>`
  (e.g. `prod-75092ec…`, build `8117098`) — the frontend build id.
- `oai-language: en-US`, `oai-session-id: <uuid>` (per page load)
- `x-openai-target-path` / `x-openai-target-route` = the request path
  (edge routing).
- Standard desktop `user-agent` (the CloakBrowser stealth UA — Windows Chrome
  146 string).

On the send specifically, additionally: `oai-echo-logs`, `oai-telemetry`,
`x-oai-turn-trace-id: <uuid>`, and the three `openai-sentinel-*` tokens.

## Sentinel (anti-bot) flow

Two-step, both `POST` (see `samples/key_exchanges.redacted.json`):

1. `POST /backend-api/sentinel/chat-requirements/prepare`
   → `{"persona":"chatgpt-freeaccount","prepare_token":"gAAAAA…"}`
   (`prepare_token` is a Fernet token; the PoW challenge/seed is derived from
   this + client work.)
2. Client computes the **proof-of-work** and a **Turnstile** solve, then:
   `POST /backend-api/sentinel/chat-requirements/finalize`
   → `{"persona":"chatgpt-freeaccount","token":"<chat-requirements-token>",
   "expire_after":540,"expire_at":<epoch>}`.

The `token` becomes the `openai-sentinel-chat-requirements-token` header; it
lives ~9 min (`expire_after: 540`). The proof-of-work result becomes
`openai-sentinel-proof-token`; the Turnstile solve becomes
`openai-sentinel-turnstile-token`.

(Note: the prepare/finalize request *bodies* showed as empty to Playwright —
the tokens likely flow via headers/response chaining; needs a closer capture
if we implement server-side minting.)

## Send: `POST /backend-api/f/conversation`

Request body (real captured sample, non-secret — this IS the schema we map to):

```json
{"action":"next",
 "messages":[{"id":"<uuid>","author":{"role":"user"},
   "create_time":<epoch_float>,
   "content":{"content_type":"text","parts":["Reply with exactly: pong"]},
   "metadata":{...}}],
 "parent_message_id":"client-created-root",
 "model":"auto",
 "timezone_offset_min":180,"timezone":"America/Sao_Paulo",
 "conversation_mode":{"kind":"primary_assistant"},
 "enable_message_followups":true,"system_hints":[],
 "supports_buffering":true,"supported_encodings":["v1"],
 "client_contextual_info":{...},"force_parallel_switch":"auto"}
```

- `model:"auto"` in the request; the **resolved model came back as
  `gpt-5-5`** (field `resolved_model_slug`/`model_slug` in the response).
- `parent_message_id:"client-created-root"` for a new conversation; follow-ups
  reference the prior assistant message id.

### Response: SSE, `Content-Type: text/event-stream`, `v1` delta encoding

```
event: delta_encoding
data: "v1"

data: {"type":"resume_conversation_token", "conversation_id":"<uuid>", ...}
data: {"type":"message_marker", ...}

event: delta
data: {"p":"", "o":"add", "v":{"message":{ ...author.role=assistant,
        content.parts:["pong"], status:"finished_successfully",
        metadata:{resolved_model_slug:"gpt-5-5", ...} }},
       "conversation_id":"<uuid>"}, "c":0}

data: {"type":"message_marker", "marker":"last_token", ...}
data: {"type":"title_generation", "title":"Pong response", ...}
data: {"type":"server_ste_metadata", "metadata":{...plan_type:"free"...}}
```

- Encoding `v1`: each `event: delta` carries a JSON-patch-like op — `o` =
  operation (`add`/`append`/`patch`), `p` = JSON path (e.g. into
  `/message/content/parts/0`), `v` = value, `c` = a sequence counter. First
  delta usually `o:"add"` the whole message skeleton; subsequent token chunks
  `append` to the parts path. (Our test reply was one word so it arrived in a
  single `add`.)
- Terminal signals: `message_marker` with `marker:"last_token"` and the
  message `status:"finished_successfully"` / `end_turn:true`.
- This maps cleanly to OpenAI streaming: each appended token → a
  `chat.completion.chunk` with `choices[0].delta.content`; `end_turn` →
  `finish_reason:"stop"`.

## Model list: `GET /backend-api/models`

Returns `{"models":[{"slug","title","max_tokens","description",
"capabilities","product_features",...}]}`. Seen slug: `gpt-5-3` ("GPT-5.3").
(Full list was truncated by our 20 KB body cap — re-capture in full when
wiring `/v1/models`.)

## OpenAI-compatible mapping (target for the proxy)

| OpenAI                         | ChatGPT web backend                              |
|--------------------------------|--------------------------------------------------|
| `POST /v1/chat/completions`    | `POST /backend-api/f/conversation` (SSE)         |
| `messages[]` (roles)           | `messages[].author.role` + `content.parts[]`     |
| `model`                        | `model` (`auto` or a `slug`); resolved in resp   |
| `stream:true` chunks           | SSE `event: delta` append ops → delta.content    |
| `finish_reason:"stop"`         | `end_turn:true` / `last_token` marker            |
| `GET /v1/models`               | `GET /backend-api/models` (map `slug`→`id`)      |

## Gotcha found: Playwright evicts response bodies on navigation

`response.text()` failed with *"No resource with given identifier found"* for
requests that happened before a later `page.goto`. **Fix for next capture:**
read bodies immediately in the `response` handler, or use a CDP session
(`Network.enable` + `Network.getResponseBody` on `Network.loadingFinished`),
and **do not navigate** between the calls of interest and the body read. The
authed exchanges we needed survived because they occurred after the last nav.

## Architecture decision — DECIDED (2026-07-10)

Because `f/conversation` needs a single-use **Turnstile** token + **PoW**,
the proxy cannot be pure server-side HTTP. **Chosen: browser-backed.**

- **A. Browser-backed — CHOSEN.** OpenAI-compatible HTTP server wraps a live
  authenticated CloakBrowser session; the browser mints the sentinel/Turnstile
  tokens (trigger a real send + capture the SSE via CDP). Robust; slower;
  needs the browser process up.
- B. Pure HTTP + reverse-engineered PoW + external Turnstile solver —
  rejected (Turnstile unsolvable without a browser/paid solver; fragile).
- C. Hybrid (browser mints tokens, server does the POST/SSE) — deferred as a
  possible later optimization once A works.

Also decided:
- **Stateful conversation:** keep one ChatGPT `conversation_id` /
  `parent_message_id`; send only the newest user message per OpenAI call
  (do not replay full `messages[]` history).
- **Models:** expose the real ChatGPT slugs from `GET /backend-api/models`
  as-is (no aliasing to OpenAI names).

## Next

- [ ] User picks A/B/C.
- [ ] Re-capture with immediate-body-read to get: full `models` list, the
      prepare/finalize request bodies, and the PoW seed/difficulty (only
      needed for B/C).
- [ ] Prototype the chosen path end-to-end (send a prompt via the proxy,
      stream an OpenAI-shaped response).
