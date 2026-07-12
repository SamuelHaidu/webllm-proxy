# The Copilot / Sydney Chat Protocol — Reference

**Status:** reverse-engineered, community + first-party HAR captures (2026-07).
**Scope:** the WebSocket chat protocol(s) behind Microsoft's Copilot family —
consumer (`copilot.microsoft.com`) and Microsoft 365 BizChat
(`m365.cloud.microsoft` / `substrate.office.com`). The SignalR protocol in §1 is
historically Bing's "Sydney" hub; the deprecated Bing endpoint itself
(`sydney.bing.com`) is **out of scope**. Written as an implementer's spec for a
single universal client (`webllm_proxy/providers/copilot/`).

> This document describes an **undocumented, unofficial** interface. It changes
> without notice and its use may violate Microsoft's Terms of Service. It exists
> here only to support the project's stated goal (bridging login-only web LLMs
> the user already has access to). No credentials, tokens, or private captures
> are reproduced here.

---

## 0. Executive summary

There are **two wire protocols** and **two supported editions** that speak them:

| Edition | Host / chat endpoint | Wire protocol | Auth | Anti-bot |
|---------|----------------------|---------------|------|----------|
| **Consumer** (free/Pro) | `wss://copilot.microsoft.com/c/api/chat?api-version=2` | **Event JSON** (§2) | `accessToken` query (AAD if signed-in; anon session otherwise) | **Cloudflare Turnstile** (HTTP) + **hashcash** PoW (in-band) |
| **M365 BizChat** (consumer & enterprise) | `wss://substrate.office.com/m365Copilot/Chathub/{conversationId}` | **SignalR "Sydney"** (§1) | AAD **Bearer** in `access_token` query (scope `sydney/v2/.default`, **8 h**) | **none** |

(The deprecated Bing endpoint `wss://sydney.bing.com/sydney/ChatHub` spoke the
same §1 protocol with cookie `_U` + `conversationSignature` auth; it is **out of
scope** — its public clients are archived. §1's field notes flag the few
Bing-only differences for reference.)

The **core** of any client is identical across editions: open a socket, send a
user turn, consume a token stream, get a final message with citations +
suggested replies + throttling. Everything that differs — the framing, the auth,
the conversation-create call, the anti-bot step, the model-selection field — is
**edition-specific** and should live behind an adapter (see the `copilot` provider).

---

## 1. Wire protocol A — SignalR "Sydney" ChatHub

A thin use of the **SignalR JSON hub protocol**. Records within a frame are
delimited by the ASCII **record separator** `0x1E` (`"\x1e"`). Every record is a
JSON object with a numeric `type`.

### 1.1 SignalR message types

| `type` | Name | Direction | Meaning |
|--------|------|-----------|---------|
| — | handshake | C→S then S→C | `{"protocol":"json","version":1}` then `{}` |
| 6 | Ping | both | keepalive |
| 4 | StreamInvocation | C→S | **the chat request** (`target:"chat"`) |
| 1 | Invocation | S→C | **streaming update** (`target:"update"`) |
| 2 | StreamItem | S→C | **final message** (`item:{…}`) |
| 3 | Completion | S→C | stream closed (`invocationId`) |
| 7 | Close | S→C | hub closing (optional `error`) |

### 1.2 Handshake

1. C→S: `{"protocol":"json","version":1}\x1e`
2. S→C: `{}\x1e`
3. C→S: `{"type":6}\x1e` (ping) — optional but the web client does it.

### 1.3 Chat request (`type:4`, `target:"chat"`)

```jsonc
{
  "arguments": [{
    "source": "officeweb",              // Bing legacy: "cib"
    "scenario": "OfficeWebPaidConsumerCopilot", // legacy: "CopilotMicrosoftCom"
    "optionsSets": ["…"],               // feature flags (see 1.6)
    "allowedMessageTypes": ["Chat","Progress","GenerateContentQuery","…"],
    "sliceIds": [],
    "message": {
      "author": "user",
      "inputMethod": "Keyboard",
      "text": "<PROMPT>",
      "messageType": "Chat",
      "locale": "en-gb",
      "locationInfo": { "timeZone": "…", "timeZoneOffset": -3 },
      "entityAnnotationTypes": ["People","File","Event","Email","TeamsMessage"],
      "imageUrl": null, "originalImageUrl": null   // set for image attachments
    },
    "tone": "Magic",                    // MODEL SELECTOR (see 1.5)
    "plugins": [{ "Id": "BingWebSearch", "Source": "BuiltIn" }],
    "conversationId": "<cid>",
    "conversationSignature": "",        // legacy: required (from create); M365: empty
    "participant": { "id": "<clientId>" }, // legacy only
    "isStartOfSession": true,           // true on the first turn only
    "streamingMode": "ConciseWithPadding",
    "disconnectBehavior": "continue"
  }],
  "invocationId": "0",                  // increments per turn, as a string
  "target": "chat",
  "type": 4
}
```

Context injection (legacy): add
`arguments[0].previousMessages=[{author:"user",description:"<ctx>",contextType:"WebPage",messageType:"Context"}]`.

### 1.4 Streaming updates (`type:1`, `target:"update"`)

```jsonc
{ "type":1, "target":"update", "arguments":[{
    "messages":[{
      "text":"<TEXT SO FAR>",          // ⚠ CUMULATIVE — full text each frame
      "author":"bot",
      "messageType":"Chat",            // or "Progress","SearchQuery","GenerateContentQuery"…
      "contentType":"EarlyProgress",   // present on progress frames
      "adaptiveCards":[…],             // search/citation cards
      "messageId":"…", "requestId":"…"
    }],
    "requestId":"…", "nonce":"…"
}]}
```

**Delta semantics: cumulative.** Each update carries the *entire* answer so far;
a client emits incremental deltas by diffing against the previous text. Progress
frames (`messageType:"Progress"`, e.g. "Taking a look…") and search frames come
interleaved and should be filtered from the visible answer.

### 1.5 Final message (`type:2`) then `type:3`

```jsonc
{ "type":2, "invocationId":"0", "item":{
    "messages":[ {…user echo…}, {
      "text":"<FULL ANSWER>",
      "author":"bot", "turnState":"Completed", "contentOrigin":"DeepLeo",
      "adaptiveCards":[…], "sourceAttributions":[…],  // citations
      "suggestedResponses":[{ "text":"…","messageType":"Suggestion" }]
    }],
    "conversationId":"…",
    "defaultChatName":"…",
    "throttling":{ "numUserMessagesInConversation":2,
                   "maxNumUserMessagesInConversation":600 },
    "result":{ "value":"Success" }     // or "Throttled" | "CaptchaChallenge"
}}
```

`result.value` is the error channel: `Throttled` (rate limited),
`CaptchaChallenge` (must solve a captcha in the browser). Then `{"type":3,"invocationId":"0"}`
closes the stream.

### 1.6 `optionsSets` and `tone` vocabulary

`optionsSets` are opaque server feature flags. (For reference, the archived
`sydney.py` shows the shape via legacy Bing style-sets — Creative →
`h3imaginative,clgalileo,gencontentv3`; Balanced → `galileo`; Precise →
`h3precise,clgalileo`; plus always-on `nlu_direct_response_filter, deepleo,
responsible_ai_policy_235, enablemm, dv3sugg`.)

M365 BizChat `tone` values (the model selector, from the capability manifest):

| `tone` | Menu label | Notes |
|--------|-----------|-------|
| `Magic` | Auto | default; "decides how long to think" |
| `Chat` | Quick Response | no reasoning |
| `Reasoning` | Think Deeper | reasoning |
| `Gpt_5_5_Chat` | GPT 5.5 Quick | explicit model |
| `Gpt_5_5_Reasoning` | GPT 5.5 Think Deeper | explicit model + reasoning |

M365 `optionsSets` observed live include: `cwc_code_interpreter*` (Python),
`cwc_flux_image` / `cwc_flux_v3` (image gen), `update_memory_plugin`,
`add_custom_instructions`, `rich_responses`, `pages_citations`.

---

## 2. Wire protocol B — Copilot Event JSON (`/c/api/chat`)

The rewritten consumer protocol. **No `\x1e` framing** — each WebSocket text
frame is exactly one JSON object with an `"event"` field. Cleaner than SignalR
and, importantly, **streams true incremental deltas**.

### 2.1 Connection

`wss://copilot.microsoft.com/c/api/chat?api-version=2&clientSessionId=<uuid>&accessToken=<token>`

`accessToken` (camelCase, ~1.7 KB) authorizes the socket; `clientSessionId` is a
per-tab UUID. A conversation id is obtained first from `POST /c/api/start` (§2.5).

### 2.2 Client → server events

| `event` | Fields | Purpose |
|---------|--------|---------|
| `setOptions` | `supportedFeatures[], supportedCards[], supportedUIComponents{}, supportedActions[]` | capability negotiation (send once, first) |
| `reportLocalConsents` | `grantedConsents[]` | consent state |
| `send` | `conversationId, content[], mode, context{}` | **the chat turn** |
| `challengeResponse` | `token, method` | anti-bot answer (§2.4) |
| `ping` | — | keepalive |

`send.content` is an array of typed parts (multimodal): `[{"type":"text","text":"…"}]`,
plus image/file parts. `send.mode` is the **model selector** — observed values:
`smart` (default), `deep-research` (a.k.a. `deep-research-nano`), `copilot-beta`.
`send.context` carries page/grounding context.

### 2.3 Server → client events

| `event` | Fields | Meaning |
|---------|--------|---------|
| `connected` | `requestId, id` | socket ready |
| `challenge` | `method, parameter, id` | **anti-bot challenge** (§2.4) |
| `received` | `conversationId, messageId, createdAt, id` | turn accepted |
| `startMessage` | `conversationId, messageId, createdAt, id` | answer begins |
| `appendText` | `messageId, partId, text, id` | **incremental delta** — append `text` |
| `partCompleted` | `messageId, partId, …` | one content part done |
| `done` | `messageId, id` | answer complete |
| `titleUpdate` | `conversationId, title, id` | auto-generated chat title |
| `pong` | `id` | keepalive reply |

**Delta semantics: incremental.** `appendText.text` is the *new* chunk; the
client concatenates. `partId` groups chunks into parts (a message may have
several parts: text, cards, etc.).

### 2.4 Anti-bot: hashcash proof-of-work

After a `send`, the server may reply with:

```json
{ "event":"challenge", "method":"hashcash", "parameter":"<sha256hex>:<bits>", "id":"0.0001" }
```

The client solves a **hashcash**-style PoW and replies:

```json
{ "event":"challengeResponse", "token":"<nonce>", "method":"hashcash" }
```

The `parameter` is `"<resource>:<difficulty>"`. The client searches for a `token`
(nonce, ASCII) such that `sha256(parameter + token)` satisfies the difficulty
(observed `:1` → trivial, `token:"0"` sufficed). Higher difficulties require more
work. This is **separate** from the HTTP-layer **Cloudflare Turnstile** guarding
the page/`/c/api/*` calls (that one needs a real/stealth browser to pass; it is
the interactive CAPTCHA seen manually).

### 2.5 Conversation lifecycle (HTTP, `/c/api/*`)

- `POST /c/api/start` — create/attach. Body `{timeZone, startNewConversation:true,
  teenSupportEnabled, correctPersonalizationSetting, performUserMerge,
  deferredDataUseCapable}`. Returns `currentConversationId, userId, isNewUser,
  isBlocked, remainingTurns, features[], cohortStatus`.
- `GET /c/api/conversations` — list; `…/{id}/history`; `…/{id}/autosuggest`.
- `GET /c/api/config` — `maxTextMessageLength, voices, …`.
- `GET /c/api/user` — `remainingUsage:{reasoningCalls, …}` (entitlements/quota).
- Notifications: a **second** socket, Azure Web PubSub
  `wss://<region>.webpubsub.azure.com/client/hubs/notifications` (events
  `system`/`sequenceAck`) — out-of-band, not needed for chat.

---

## 3. Editions (the differences to isolate)

### 3.1 Consumer — `copilot.microsoft.com`
- Protocol **B**. Create via `POST /c/api/start` → `conversationId`.
- Auth: `accessToken` query param. Signed-in = AAD token (via
  `login.microsoftonline.com/common/oauth2/v2.0/token`); free/anon = an
  anonymous session token minted by `/c/api/start`/page.
- Anti-bot: Cloudflare Turnstile (browser) + hashcash (in-band).
- Model: `send.mode` (`smart` | `deep-research` | …). Quota in `/c/api/user`.

### 3.2 M365 BizChat — `substrate.office.com`
- Protocol **A** (SignalR). Endpoint
  `wss://substrate.office.com/m365Copilot/Chathub/{conversationId}?…&access_token=<Bearer>`.
- Auth: AAD **Bearer**, scope `https://substrate.office.com/sydney/v2/.default`,
  tenant `9188040d-…` (consumer MSA) or an enterprise tenant. **TTL 8 h**
  (`expires_in=28799`); the token is an encrypted **JWE** (5-part) so `exp` is
  only readable from the `/token` response. Minted client-side by MSAL.
- `conversationId` comes from the shell (`POST m365.cloud.microsoft/chat` action
  set) or a Sydney create call; `conversationSignature` observed **empty** (the
  Bearer replaces it).
- Anti-bot: **none**.
- Model: `tone` (`Magic` | `Chat` | `Reasoning` | `Gpt_5_5_*`). Tools are
  server-side plugins toggled via `executionControls`; **extensibility disabled**
  on consumer — not a client-declarable `tool_use` surface.

### 3.3 Bing legacy — `sydney.bing.com` (out of scope)

Deprecated and **not implemented**. It spoke protocol **A** with cookie `_U` +
`conversationSignature` auth (created via `POST …/turing/conversation/create`)
and surfaced captchas via `type:2 item.result.value = "CaptchaChallenge"`. The
public clients that targeted it
([EdgeGPT](https://github.com/acheong08/EdgeGPT),
[ReEdgeGPT](https://github.com/Integration-Automation/ReEdgeGPT),
[sydney.py](https://github.com/vsakkas/sydney.py)) are all **archived**. Because
M365 reuses the same §1 protocol, a bing edition could be re-added later, but the
client here deliberately ships only the two working editions.

---

## 4. Normalized model (what a universal client exposes)

Regardless of edition, one turn produces:

- a stream of **text deltas** (incremental, after normalizing A's cumulative
  frames to diffs);
- optional **progress/among** events (search queries, tool/plugin triggers,
  generated code, image generation);
- a **final** payload: full text, **citations** (`sourceAttributions` /
  adaptiveCard references / consumer cards), **suggested replies**, and
  **throttling** (`num/max` messages, or consumer `remainingUsage`);
- **error signals**: throttled, captcha/challenge required, conversation limit.

Model selection normalizes to an edition-agnostic enum, e.g. `AUTO`,
`FAST`, `THINK`, `RESEARCH`, `MODEL(<id>)`, mapped per edition to `tone` or
`mode`.

See `webllm_proxy/providers/copilot/README.md` for the API that implements this model.

---

## 5. Live findings (M365, 2026-07-11) and open items

**Confirmed live** by replaying a fresh 8 h Bearer + conversationId from a plain
Python `websockets` client (see the `copilot` provider):

- ✅ **Replay binding: NONE (M365).** The socket accepts a token+conversationId
   lifted out of the browser from a non-browser TLS client. No connect-time
   anti-bot, no device/session binding. `Chat` (instant) and `Reasoning`
   (thinking, with full `optionsSets`) both returned real answers.
- ⚠️ **`optionsSets` is server-validated.** An empty/sparse list → `type:2`
   `item.result.value = "InvalidRequest"` ("Sorry, I wasn't able to respond to
   that") for *any* `tone` — send the full realistic set.
- ⚠️ **Request-level abuse clamp.** After ~20 rapid programmatic turns the
   backend returned `InvalidRequest` for *all* turns (incl. the cheapest model
   and trivial prompts), not clearing within ~25 s. Not a captcha; a downstream
   soft-block. Pace requests / prefer a browser-backed transport for volume.
- ⚠️ **Minimal invocation insufficient.** A faithful capture-shaped invocation
   (with `sessionId`/`clientInfo`/`streamingMode`/correlation ids) was accepted
   while a minimal one was rejected — the client's M365 invocation needs those
   fields to be reliable.

**Still open:**

1. **Consumer anon token**: exact source/shape of `accessToken` for a signed-out
   free session (from `/c/api/start`? a separate token endpoint?).
2. **hashcash** at difficulty > 1: confirm the digest input ordering
   (`parameter+token` vs `token+parameter`) and leading-zero-bits vs
   leading-zero-hex-chars.
3. **M365 conversationId creation** off-browser: whether a Sydney create call
   exists or the id must come from the `m365.cloud.microsoft/chat` shell action.
4. **Abuse-clamp thresholds**: turns/min before the soft-block, and how long it
   lasts; whether it is conversation- or token/account-scoped.
5. **Enterprise M365**: same substrate endpoint with a work tenant + different
   `scenario`/`licenseType`; capture to confirm no divergence.
