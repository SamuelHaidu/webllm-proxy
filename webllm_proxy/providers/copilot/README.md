# copilot

A client for **Microsoft Copilot's chat protocols** (M365 BizChat + consumer)
with a familiar **OpenAI-SDK-style API**, plus wiring as the `copilot`
**provider** inside `webllm_proxy`. One surface over two wire protocols (SignalR
"Sydney" and the consumer event protocol); every per-edition difference (auth,
endpoint, anti-bot, model selector) is isolated behind an edition adapter.

Protocol reference: [`../../../docs/protocol/copilot-protocol.md`](../../../docs/protocol/copilot-protocol.md).

> Unofficial, reverse-engineered. Use only against an account you own; this may
> violate Microsoft's ToS and can break without notice.

## What it covers

| Edition | Host | Wire protocol | Auth | Anti-bot |
|---|---|---|---|---|
| `m365` | substrate.office.com | SignalR "Sydney" | AAD Bearer (`access_token`, 8 h) | none (connect-time) |
| `consumer` | copilot.microsoft.com | event JSON | `accessToken` query | Cloudflare Turnstile + hashcash |

## Quickstart — OpenAI-style API (primary)

```python
from webllm_proxy.providers.copilot import Copilot   # sync, mirrors `openai.OpenAI`

client = Copilot(edition="m365", api_key=TOKEN, conversation=CONVERSATION_ID)

resp = client.chat.completions.create(
    model="think",                                    # auto/fast/think/research OR a raw id
    messages=[{"role": "user", "content": "summarize my week"}],
)
print(resp.choices[0].message.content)

for chunk in client.chat.completions.create(
    model="fast", messages=[{"role": "user", "content": "hi"}], stream=True,
):
    print(chunk.choices[0].delta.content or "", end="")

for m in client.models.list():                        # .data list; ModelInfo(.id/.title/.reasoning)
    print(m.id, m.title)
```

`AsyncCopilot` is the async twin (mirrors `openai.AsyncOpenAI`). `api_key` is the
browser-minted token (edition picks the query param); `conversation` is the
ChatHub id (required for M365; consumer can auto-create). Divergences from
OpenAI: only the last `user` message is sent per turn (Copilot keeps history
server-side); `system` messages are prepended as context; no token `usage`
(`throttling` exposed instead).

## As a `webllm_proxy` provider

Wired like `chatgpt`/`databricks` — selectable on the CLI:

```
webllm-proxy login  --provider copilot     # opens m365 (or consumer) to log in once
webllm-proxy serve  --provider copilot      # headless; exposes an OpenAI surface (port 5104)
#   GET  /v1/models
#   POST /v1/chat/completions
```

The provider runs turns **through the shared CloakBrowser session**: it types
the message into the page, the page opens its ChatHub WebSocket, and
`transport/browser.py` captures the server->client frames over CDP (the
`CopilotAccumulator` parses them via the same protocol codecs). Running in the
real logged-in page reuses the existing browser infra and avoids the M365
abuse-clamp that raw off-browser replay triggers. Config via `COPILOT_PROXY_*`
env (`EDITION`, `URL`, `PROFILE`, `HEADLESS`, `HOST`, `PORT`).

## Architecture

```
webllm_proxy/providers/copilot/
  sdk.py           PRIMARY API: Copilot / AsyncCopilot, chat.completions, models
  provider.py      CopilotProvider — the webllm_proxy provider (browser-backed)
  routes.py        OpenAI /v1/chat/completions + /v1/models for the provider
  accumulator.py   captured WS frames -> proxy Event stream (via codecs)
  config.py        env-driven provider config
  client.py        CopilotClient — the low-level async engine (advanced)
  models.py        normalized types: Model, ModelInfo, Delta, Progress, Final, …
  transport.py     Transport ABC + WebsocketsTransport (standalone library path)
  auth.py hashcash.py exceptions.py
  protocol/        WIRE PROTOCOLS: SignalRCodec (Sydney), EventCodec (consumer)
  editions/        THE DIFFERENCES: m365.py, consumer.py (+ base, registry)
  tests/           offline codec + SDK tests (validated against real captures)
```

The engine core never knows which edition it drives: open socket ->
`open_frames()` -> `encode_send()` -> pump `decode()` until `Final`. Cumulative
SignalR text and incremental consumer text both normalize to incremental
`Delta`s.

## Advanced: the low-level engine

`CopilotClient(edition, credential).ask(...)`/`ask_text(...)` and the codecs stay
exported for direct use; the `Copilot` SDK is a thin façade over them. A custom
`Transport` (e.g. browser/CDP) plugs in via `transport_factory=`.

## Install

`websockets` (default transport) is the `copilot` extra; `httpx` is only needed
for `create_conversation()`/live model discovery:

```
uv sync --extra copilot        # or: pip install "webllm-proxy[copilot]"
```

## Status / limitations

- Codecs **validated against real captures** (M365 993-char and consumer
  1587-char answers reconstruct exactly; offline tests in `tests/`).
- **Live-tested against M365:** off-browser replay of a lifted 8 h Bearer +
  conversationId from a plain `websockets` client works (no connect-time
  anti-bot / no session binding). But: `optionsSets` is server-validated (empty
  → `InvalidRequest`), and a **request-level abuse clamp** kicks in after a burst
  of rapid programmatic turns — pace requests; the browser-backed provider path
  is the robust one.
- Provider v1: model/tone selection is accepted on the request but not yet forced
  in the page UI; composer selectors + richer conversation continuity are the next
  live-tuning pass.
- Server-side tools (search, Python, image gen) run automatically; this is a
  **chat** client, not a client-driven `tool_use` agent surface.
```
