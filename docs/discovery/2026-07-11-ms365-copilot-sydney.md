# Microsoft 365 Copilot (BizChat consumer) web chat — HAR scoping (2026-07-11)

**New target, third backend candidate** (after `chatgpt` and `databricks`).
Same project goal: expose a login-only web LLM as a local API. This is the
**pre-browser** analysis, derived entirely from one captured HAR, so a later
live session knows exactly what to look for.

TL;DR: the Copilot chat turn is a **SignalR ChatHub over WebSocket** to
`substrate.office.com/m365Copilot/Chathub/{conversationId}` ("Sydney"
backend), authorized by an **OAuth Bearer** token (scope
`substrate.office.com/sydney/v2/.default`), **no anti-bot**. A bridge is
feasible but is the hardest of the three: the stream is WebSocket frames (the
current CDP transport only captures HTTP), and Copilot's tools are server-side
and fixed (extensibility disabled) so it maps to a **chat** model, not a
native `tool_use` agent backend like Databricks.

**UPDATE (same day): the full ChatHub WebSocket protocol was captured** in a
second HAR (`websocket_copilot_ms365__m365.cloud.microsoft.har`, gitignored) and
is documented in the "Update" section at the bottom. It is the well-known
**Sydney/BingChat SignalR protocol** (`tone` = model, cumulative text deltas,
final `StreamItem`+`Completion`). Nothing about the transport is a mystery
anymore; only token+conversationId acquisition and off-browser replay-binding
remain to confirm live.

## Source & tooling

- Capture: `docs/discovery/copilot_ms365__m365.cloud.microsoft.har`
  (5.6 MB, **gitignored** — live session context; never commit).
- Explorer: `scripts/har_explore.py` (paths/list/show/req/resp/headers/keys/grep,
  secret-redacted). **Gotcha found this session:** `grep` compiles the pattern
  with Python `re`, so alternation is `a|b|c` — a `\|` is a *literal pipe* and
  silently matches nothing. Two early greps here were false negatives from that.

## What this HAR does and does NOT contain

The capture is the **M365 shell load + idle Copilot page**, plus the tail end of
one real chat ("what is the tool calling available here?"). It contains the
app shell, capability manifest, conversation list, OAuth token exchanges, and
telemetry. It does **NOT** contain the chat completion itself:

- Grepping every response body for the assistant's actual answer text
  (`broad categories`, `futuristic city`, `SharePoint-connected`,
  `Generate an image`) → **zero hits**. The answer is not in any HTTP body.
- No WebSocket handshake (`status 101`) and no `wss://` request was recorded.

Conclusion: the chat turn rides a **WebSocket/SignalR ChatHub** on a connection
the HAR didn't log (HAR records the HTTP negotiate at most, not WS frames), with
**Trouter** as the async notify channel. That is why an HTTP HAR looks "empty"
of chat.

## Endpoints seen (host+path)

- `POST m365.cloud.microsoft/chat` — **NOT chat completion.** SSR/data endpoint,
  body `{"action": "RefreshNavPane" | ...}`. Returns the app store (pinned
  apps), the **capability manifest**, and the **conversation list**.
- `POST m365.cloud.microsoft/events` — client telemetry (`action`-keyed,
  `EventV2`). This is where the Sydney lifecycle + the ChatHub URL leaked.
- `POST login.microsoftonline.com/9188040d-.../oauth2/v2.0/token` — MSAL token
  mint. Tenant `9188040d-6c67-4c5b-b112-36a304b66dad` = the well-known
  **consumer/MSA** tenant. One token scope is
  `https://substrate.office.com/sydney/v2/.default`.
- `POST edge.skype.com/registrar/prod/V3/registrations` + `go.trouter.skype.com`
  + `pub-csm-usea-01-f.trouter.skype.com:3443/v4/...` — **Trouter** real-time
  channel. Registration body: `appId: "bizchat"`, `templateKey: "bizchat_5.0"`,
  `productContext: "CopilotConsumer"`, `platform: 3639/1.0.0`.
- `substrate.office.com/search/api/v1/suggestions|userconfig`,
  `graph.microsoft.com/v1.0/me/...`, `*.events.data.microsoft.com/OneCollector`
  — search autosuggest, profile/drive, telemetry. Not chat.

## The chat backend: "Sydney"

Multiple independent breadcrumbs, all pointing at the same place:

1. **Chat hub URL** (leaked in a telemetry event body, req 46):
   `https://substrate.office.com/m365Copilot/Chathub/{conversationId}`
   where `conversationId` looks like `00000000-0000-0000-8d5d-2e2a8ac750f8@84...`
   — note `8d5d-2e2a8ac750f8` == the user's OneDrive `cid` (`8D5D2E2A8AC750F8`)
   seen in the graph drive call, `@84...` a tenant/puid suffix. The conversation
   id encodes the user object id.
2. **Token audience**: MSAL mints an access token with scope
   `https://substrate.office.com/sydney/v2/.default`. That bearer token is what
   authorizes the chat.
3. **Client lifecycle** (telemetry `eventId`s): `SydneyClientWarmup...`,
   `SydneyGetChatApiTokensStart` → `SydneyGetChatApiTokensFinish`,
   `isSydneyTokenSet: true`, `SydneyTokenExpirationTimeFail`, plus config flags
   `enableSydneyBlockList`. The client warms up a Sydney client, fetches chat
   API tokens, then connects the hub.

So: **MSAL → Sydney bearer token → SignalR ChatHub WebSocket at
`m365Copilot/Chathub/{cid}` → streamed deltas; Trouter for async notify.**
This is the classic Bing-Chat/Sydney shape adapted to M365 BizChat.

## Auth model (vs the other two providers)

- `chatgpt`: per-request Turnstile/PoW tokens minted in-browser (anti-bot heavy).
- `databricks`: session **cookie + `x-csrf-token` + org-id**, no anti-bot.
- `ms365 copilot`: **OAuth Bearer** (`Authorization: Bearer <MSAL token>`,
  scope `sydney/v2/.default`), **no Turnstile/PoW**. Token minted client-side by
  MSAL on the consumer tenant. **TTL = 8 h** (`expires_in` and `ext_expires_in`
  both `28799`s in every Sydney token-endpoint response; the token is a 5-part
  **JWE**, encrypted, so `exp` isn't readable client-side — 8 h is from the
  `/token` response, authoritative). MSAL silently re-mints behind the SSO
  cookie/refresh token. There may be a per-conversation
  `conversationSignature` (present as a field but **empty** in this idle
  capture) — an anti-replay/binding token to confirm live. A `bcwaf` cohort tag
  in `x-client-eligibility` hints at a Bing-Chat WAF in front.

## Capability manifest (answers "what tool calling is available")

From `POST /chat` (`action: RefreshNavPane`) response,
`store.bizchatAsAgentGpt.clientPreferences`:

- **Model selector** (`modelSelectorMetadata`, default `defaultModelSelectionId: "Magic"`):
  | id | title | note |
  |----|-------|------|
  | `Magic` | Auto | "Decides how long to think" (default) |
  | `Chat` | Quick Response | answers right away |
  | `Reasoning` | Think Deeper | reasoning |
  | `Gpt_5_5_Chat` | GPT 5.5 Quick Response | explicit GPT-5.5 |
  | `Gpt_5_5_Reasoning` | GPT 5.5 Think Deeper | explicit GPT-5.5 reasoning |
- **Tool toggles** (`executionControls`): `connectors`, `work`, `web`,
  `personalOneDrive`, `builtInPlugins`, `localDevice`. These are **server-side
  plugins**, toggled on/off — not client-declared function tools.
- **Context pills** (`inputControlConfiguration.allowedCIQPills`, "Context IQ"):
  `People, Files, Meetings, Emails, Chats, Channels, Other` — the `@`-mention
  grounding sources.
- Flags: `isCopilotExtensibilityDisabled: true`, `isAgentBuilderDisabled: true`,
  `isEURegion: true`. **Extensibility is disabled** → the client cannot inject
  arbitrary tools; the toolset is fixed by the service.

The assistant's own self-description (M365 search over mail/files/calendar/
people, web search+browse, image gen, page/doc/xls/ppt/pdf creation, Python
execution, connectors, memory) matches these server-side `executionControls`.

## Conversation store schema (`POST /chat`, conversation list)

`store.conversationPageHistoryList.chats[]`:
`conversationId, chatName, conversationSignature, threadId, plugins[], tone,
createTimeUtc, updateTimeUtc, expiryTimeUtc, groupType, isUnread,
threadLevelGptId.id, isLegacyWebChat`. The user's real thread shows up here with
`chatName: "Available Tool Calls Inquiry"` (auto-title of their question) and an
**empty** `conversationSignature`/`threadId` at rest.

## Bridge feasibility ("is there a loophole") — assessment

**Feasible, but the hardest of the three, and different in kind.**

Reasons / the path:

1. **No anti-bot.** Unlike chatgpt, no per-request token minting; a CloakBrowser
   persistent profile logs in once (MSA) and the page's MSAL keeps the Sydney
   token fresh. Good.
2. **Transport is a WebSocket ChatHub, not SSE/HTTP.** The current
   `transport/browser.py` captures HTTP responses over CDP
   (`Network.responseReceived`/`dataReceived`/`streamResourceContent`). It does
   **not** capture WS frames. Bridging Copilot needs a transport extension:
   hook `Network.webSocketFrameReceived`/`webSocketFrameSent` for the
   `m365Copilot/Chathub` socket. This is the main new engineering.
3. **Two viable strategies:**
   - **(A) In-page driver + WS-frame capture** (mirrors `chatgpt`): drive the
     real BizChat page, submit the turn (page interaction or in-page hub
     invocation via `page.evaluate`), capture streamed deltas by hooking
     `Network.webSocketFrameReceived`. Lowest protocol-RE cost; robust to any
     per-conversation signature because the page mints/binds it.
   - **(B) Server-side Sydney client** (mirrors `databricks`): `page.evaluate`
     to pull the Sydney bearer token + `conversationId`/`conversationSignature`,
     then open the SignalR ChatHub WS from Python and speak the hub protocol
     directly. More RE (SignalR negotiate + invocation + delta schema); risk
     that a device/session-bound signature blocks off-browser replay.
4. **API shape it can expose:** best mapped to a **chat-completions** endpoint
   with model variants (`quick`=`Chat`, `think`=`Reasoning`, `gpt-5.5`=
   `Gpt_5_5_*`, `auto`=`Magic`). It is **not** a good native `tool_use` backend:
   Copilot's tools are server-side and fixed (extensibility disabled), so we
   cannot drive an arbitrary function-calling loop the way Databricks allows. It
   would be a strong M365-grounded chat/research model, weak as an agent tool
   backend.
5. **Risk/ToS:** consumer MSA Copilot; automation is against MS ToS and Sydney
   has abuse heuristics (`enableSydneyBlockList`, `bcwaf`). Rate limits and a
   possible anti-replay `conversationSignature` are unknowns.

## Open questions for a live browser session

1. **Capture the ChatHub WebSocket**: negotiate + frames for
   `wss://substrate.office.com/m365Copilot/Chathub/{cid}?...` — the SignalR
   handshake, the **invocation schema** (message text, selected model id,
   `executionControls`, CIQ pills, `conversationSignature`), and the **delta
   schema** of streamed responses (SignalR `{"type":1,...}` frames: message
   text, adaptive cards, `sourceAttributions`, any tool-invocation events).
2. Does the Sydney bearer token **alone** authorize a fresh WS from a non-browser
   client, or is a `conversationSignature`/device-bound header required
   (anti-replay)? Determines whether strategy (B) is viable.
3. Trouter's exact role: is the answer delivered over the ChatHub WS, the Trouter
   long-poll, or both?
4. Rate limits and block-list behavior; whether "Think Deeper" / "GPT 5.5" are
   entitlement-gated on this consumer account.
5. How model selection + tool toggles are actually sent on the wire (the manifest
   shows the *menu*; the invocation shows what the client *sends*).

## Verdict

Copilot is bridgeable as a **chat model** (not an agent tool backend). The only
real blocker is engineering, not anti-bot: add WebSocket-frame capture to the
transport and reverse the Sydney ChatHub SignalR schema from a live session.
Recommend strategy (A) (in-page + WS capture) first — it sidesteps the
token/signature-binding unknowns that could sink a pure server-side client.

---

## Update (2026-07-11): full ChatHub WebSocket protocol captured

Second HAR: `docs/discovery/websocket_copilot_ms365__m365.cloud.microsoft.har`
(107 KB, **gitignored**) — this one *does* carry the WS frames (Chrome exports
them under `entry["_webSocketMessages"]`, which the HTTP body view ignores). One
entry, the ChatHub socket, **71 frames** (4 client→server, 67 server→client).

**New tooling:** added `ws` / `wsshow` subcommands to `scripts/har_explore.py`
(SignalR-aware: splits the `\x1e` record separator, labels frames by SignalR
type/target, redacts secret query params like `access_token` and JSON keys incl.
`signature`/`encrypted`). Also extended `SECRET_KEY` (now catches
`signature|encrypted`) and added `redact_url()` for token-bearing query strings.
Usage: `har_explore.py FILE ws [N] [--dir send|receive]` and
`har_explore.py FILE wsshow N MSG`.

### Connect URL (redacted)

`wss://substrate.office.com/m365Copilot/Chathub/{conversationId}` with query:
`chatsessionid` (redacted), `XRoutingParameterSessionKey`, `clientrequestid`,
`X-SessionId` (redacted), `ConversationId=5b4968f2-...`,
**`access_token=<Bearer, ~1801 chars>`** (the Sydney token, in the query — this
is the auth), a large `variants=` feature-flag list, `source="officeweb"`,
`product=Office`, `agentHost=Bizchat.FullScreen`, **`licenseType=Starter`**,
`agent=web`, `scenario=OfficeWebPaidConsumerCopilot`.

### SignalR framing (json protocol, `\x1e`-delimited)

| frame(s) | dir | SignalR | meaning |
|----------|-----|---------|---------|
| 0 | →S | `{"protocol":"json","version":1}` | handshake |
| 1 | S→ | `{}` | handshake ack |
| 2 | →S | `type:6` | Ping |
| **3** | →S | **`type:4` (StreamInvocation) `target:"chat"`** | **the chat request** |
| 4–67 | S→ | `type:1` (Invocation) `target:"update"` | streamed deltas |
| 68 | S→ | `type:6` | Ping |
| **69** | S→ | **`type:2` (StreamItem) + `type:3` (Completion)** | **final message + stream end** |
| 70 | →S | `type:1 target:"Metrics"` | client telemetry |

### Request schema (frame 3, `type:4 target:"chat" invocationId:"0"`)

`arguments[0]` — the fields a bridge must send:
- **`message`**: `{author:"user", text:"<PROMPT>", messageType:"Chat", locale,
  inputMethod:"Keyboard", locationInfo, entityAnnotationTypes:[People,File,
  Event,Email,TeamsMessage]}`. `text` is the user prompt (this capture:
  "do i have premium features here?").
- **`tone`**: the **model selector** — value `"Magic"` here (= Auto). Maps to the
  manifest ids `Magic|Chat|Reasoning|Gpt_5_5_Chat|Gpt_5_5_Reasoning`.
- **`plugins`**: `[{Id:"BingWebSearch", Source:"BuiltIn"}]` — enabled server-side
  tools for the turn.
- **`optionsSets`**: ~27 capability flags (`cwc_code_interpreter`,
  `cwc_flux_image`/`cwc_flux_v3` image gen, `update_memory_plugin`,
  `add_custom_instructions`, `rich_responses`, `pages_citations`, ...).
- **`allowedMessageTypes`**: ~30 accepted stream message types (`Chat`,
  `Progress`, `GeneratedCode`, `TriggerPlugin`, `GenerateGraphicArt`,
  `SearchQuery`, `SuggestedResponses`, `EndOfRequest`, ...).
- `source:"officeweb"`, `streamingMode:"ConciseWithPadding"`, `clientInfo`
  (`clientPlatform:"mcmcopilot-web"`, `productEntryPoint:"ChatPanel"`),
  `isStartOfSession:false`, `disconnectBehavior:"continue"`.

### Streamed deltas (frames 4–67, `type:1 target:"update"`)

`arguments[0].messages[]`. First a `messageType:"Progress"`/
`contentType:"EarlyProgress"` ("Taking a look…"), then Chat deltas whose `text`
**grows cumulatively** (measured: 138→146→342→…→993 monotonic; the client
replaces, it does not append). A bridge diffs consecutive cumulative texts to
emit incremental OpenAI/Anthropic deltas. `nonce` + `requestId` on each.

### Final (frame 69)

`type:2` **StreamItem** `item`: full `messages[]` (the user echo + the bot
message with complete `text` [len 993, matches last delta], `adaptiveCards`,
`sourceAttributions:[]`, `suggestedResponses[]`, `turnState:"Completed"`,
`contentOrigin:"DeepLeo"`), plus conversation metadata: `conversationId`,
`defaultChatName:"Available Tool Calls Inquiry"`, `conversationTransferToken`
(redacted), **`throttling.maxNumUserMessagesInConversation:600`** (the per-convo
rate cap), `conversationExpiryTime`. Then `type:3` **Completion** closes the
stream. **The assistant's full answer text is present here** — so a WS capture
(unlike the HTTP HAR) does contain the answer.

### What this resolves vs. what's still open

Resolved (was "open questions" 1 & partly 3/5):
- Transport + full invocation/delta/final schema — **done**, it's Sydney/BingChat
  SignalR. Model selection = `tone`; tools = `plugins` + `optionsSets`.
- Streaming is cumulative; final is StreamItem+Completion.

Still open (needs a live session, not a HAR):
- **Token + conversationId acquisition**: mint the `sydney/v2/.default` Bearer via
  the page's MSAL, and create/obtain a `conversationId` (the HTTP `POST
  m365.cloud.microsoft/chat` action set, or a Sydney create call). 
- **Off-browser replay binding**: does the WS accept a token+conversationId from a
  plain Python `websockets` client, or is there TLS/session binding that forces
  the in-page path? (Determines strategy A vs B.)
- Whether `tone:"Reasoning"` / `Gpt_5_5_*` are entitlement-gated on this
  `licenseType=Starter` MSA account.

### Prior art / ready-made tools (web search, 2026-07-11)

There is a well-known family of reverse-engineered Sydney clients, but **none
targets our endpoint/auth, and all are archived**:

| Tool | Endpoint | Auth | Status |
|------|----------|------|--------|
| [EdgeGPT](https://github.com/acheong08/EdgeGPT) (acheong08) | `bing.com/chat`, `sydney.bing.com/sydney/ChatHub` | cookie `_U` | **archived Aug 2023** |
| [ReEdgeGPT](https://github.com/Integration-Automation/ReEdgeGPT) | `sydney.bing.com/sydney/ChatHub` | cookie JSON | **archived Dec 2024** |
| [sydney.py](https://github.com/vsakkas/sydney.py) (vsakkas) | `edgeservices.bing.com/edgesvc/chat` / `sydney.bing.com` | cookie `BING_COOKIES` | **archived Nov 2025**; last rel v0.23.1 Jan 2025 (v0.23.0 = "use old Copilot API to fix Sydney") |
| [SydneyQt](https://github.com/juzeon/SydneyQt) (juzeon) | `sydney.bing.com/sydney/ChatHub` | cookie | Go/Wails desktop, jailbreak-focused |

**Key mismatch:** every one targets the **consumer Bing Chat**
(`sydney.bing.com` / `edgeservices.bing.com` / `copilot.microsoft.com`) with
**cookie `_U` + `conversationSignature`** auth. Our capture is the **M365 BizChat
consumer** endpoint `substrate.office.com/m365Copilot/Chathub` with **AAD Bearer
(JWE, 8 h)** auth and an **empty** `conversationSignature` (the Bearer replaces
the old encrypted-signature dance that repeatedly broke these tools). So none is
usable as-is, and all are unmaintained even for their own targets.

**But the wire grammar is identical.** These repos are a valuable **reference
implementation** for the SignalR layer: message construction (the `type:4`
`chat` invocation with `optionsSets`/`tone`/`allowedMessageTypes`), the `\x1e`
framing, cumulative-delta accumulation, and citation/suggestion parsing all port
directly. The only thing to swap is the connection layer — build the
`substrate.office.com/m365Copilot/Chathub/{cid}?...&access_token=<Bearer>` URL
and drop the cookie/`conversationSignature` machinery. `sydney.py` (most
complete, still readable while archived) is the best model to crib from; check
its licence before copying code.

### Revised verdict

The protocol is **fully understood and standard** (community-documented Sydney/
BingChat SignalR). A server-side WS client (**strategy B**) is now clearly
viable given a token+conversationId: connect → `{"protocol":"json","version":1}\x1e`
→ send `type:4 target:"chat"` → read `update`/`StreamItem`/`Completion`. Expose
as a **chat-completions** model with `model`→`tone` mapping
(`quick`=Chat, `think`=Reasoning, `gpt-5.5`=Gpt_5_5_*, `auto`=Magic). Strategy A
(in-page) remains the safe first cut if replay-binding blocks B. Not a
`tool_use` agent backend (server-side fixed plugins).

---

## Update 2 (2026-07-11): consumer variant captured + universal client shipped

Captured a **third** edition: consumer `copilot.microsoft.com`
(`consumer__copilot.microsoft.com.har`, gitignored). It is a **different, newer
wire protocol** from Sydney/SignalR:

- Endpoint `wss://copilot.microsoft.com/c/api/chat?api-version=2&accessToken=…`
  (WebSocket `101`), conversation created via `POST /c/api/start`.
- **Event-JSON protocol** (no `\x1e`): client `setOptions`/`send`/`ping`/
  `challengeResponse`; server `connected`/`challenge`/`received`/`startMessage`/
  **`appendText`** (TRUE incremental deltas)/`done`/`titleUpdate`/`pong`.
- **Anti-bot**: HTTP-layer **Cloudflare Turnstile** (the manual CAPTCHA) + an
  **in-band hashcash PoW** (`{"method":"hashcash","parameter":"<hash>:<bits>"}`
  → `challengeResponse{token}`; difficulty `:1` observed, `token:"0"` sufficed).
- Model selector is `send.mode` (`smart` default, `deep-research`); `content` is
  typed parts (multimodal). Quotas in `GET /c/api/user`
  (`remainingUsage.reasoningCalls`).

So the Copilot family spans **two wire protocols** (SignalR-Sydney for
Bing-legacy + M365-substrate; event-JSON for consumer) and **three editions**.

**Shipped deliverables** (this session):
1. **`docs/protocol/copilot-protocol.md`** — full implementer's spec for both
   wire protocols + all three editions (official-doc style).
2. **`unicopilot/`** — a clean, detachable **universal client**: core
   (`client`/`protocol`/`transport`/`auth`/`hashcash`) is edition-agnostic;
   per-edition differences isolated in `editions/{consumer,m365}.py` (Bing legacy
   is out of scope — deprecated/archived — but the shared SignalR codec would let
   it be re-added).
   Two protocol codecs (`SignalRCodec`, `EventCodec`) normalize both wire formats
   to one incremental `Delta`/`Progress`/`Final` stream. Offline codec tests
   pass, and — key validation — the codecs **decode the real captured frames
   exactly**: M365 993-char answer from 30 cumulative SignalR deltas; consumer
   1587-char answer from 70 `appendText` events (2 hashcash challenges handled).
   Still needs a live off-browser run to confirm token replay-binding + anon
   consumer token source (see protocol doc §5).

Reference source cross-checked: `vsakkas/sydney.py` (archived) — its
`_build_ask_arguments`/`optionsSets`/`ConversationStyle` vocabulary informed the
SignalR codec's request shape.

---

## Update 3 (2026-07-11): LIVE test against M365 + model auto-discovery

Ran `unicopilot` against the **live** M365 ChatHub, replaying a fresh 8 h Bearer
+ conversationId (read from the gitignored HAR at runtime; token never printed;
`websockets` added via `uv --optional unicopilot`, not pip).

**Headline: off-browser replay works — M365 has NO connect-time anti-bot and NO
session binding.** A plain Python `websockets` client with the lifted token
connected and streamed real answers. This resolves the replay-binding open
question for M365.

Model-control results (probe = a real "which job offer" life question):
- `Chat` (instant) → **Success**, genuine high-quality answer, ~1.8 s to first
  token, ~5 s total.
- `Reasoning` / `Gpt_5_5_Reasoning` (thinking) → **Success** *only with the full
  `optionsSets`*; with an empty/sparse set → `InvalidRequest`. So `optionsSets`
  is **server-validated**, not decoration (the M365 edition now ships the full
  realistic set; a 6-item guess would 400).
- All models self-identify as "GPT‑5 chat model" regardless of `tone` (models
  are unreliable narrators of their own version).

**The predicted anti-bot did appear — as a request-level clamp, not a captcha.**
After ~20 rapid programmatic turns the backend began returning `InvalidRequest`
("Sorry, I wasn't able to respond to that") for **every** turn, including the
cheapest model and trivial prompts, and didn't clear within 25 s. The WebSocket
+ token still connected fine — the block is downstream, at request validation.
Lesson: pace requests / use a browser-backed transport for sustained use.

Also found: the client's **minimal invocation is insufficient** — a faithful,
capture-shaped invocation (with `sessionId`/`clientInfo`/`streamingMode`/
correlation ids) was accepted while the minimal one was rejected. Enriching the
M365 edition's invocation is the next step.

**Model auto-discovery shipped** (replaces the hardcoded list): `ModelInfo` +
`Edition.parse_models()`/`discover_models()`/`default_models()`, and
`client.list_models()`. M365 parses
`store.bizchatAsAgentGpt.…modelSelectorMetadata.availableModelSelectionOptions`
from the shell manifest (flattening the GPT `itemGroup`); consumer derives modes
from `/c/api/start` feature flags (`smart-mode-*`, `deep-research-*`).
`ask(model=…)` now also accepts a raw discovered id. The M365 parser is
**validated against the real captured manifest** (5 models: Magic/Chat/Reasoning
/Gpt_5_5_Chat/Gpt_5_5_Reasoning, correct titles/reasoning/default/family) and
covered by unit tests (9 passing).
