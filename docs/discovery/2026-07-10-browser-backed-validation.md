# 2026-07-10 — Browser-backed primitive validated (headless, stateful)

Validates the two build decisions (browser-backed + stateful) end to end on
the **already-logged-in persistent profile**, fully **headless**, no user
interaction. Probe script: `/tmp/cloak_send_probe.py`.

## Results

- **Headless persisted login works.** `launch_persistent_context(".cloak-profile",
  headless=True)` → `chatgpt.com` → `GET /api/auth/session` returns an
  `accessToken` (`AUTHED=True`). No re-login; the profile from the one-time
  headed login carries the session.
- **Send + capture works headless.** Typing into `#prompt-textarea` + Enter
  triggers the full sentinel/Turnstile/PoW flow (the frontend mints the
  tokens) and streams a reply. Captured **2 of 2** `f/conversation` SSE
  responses via a **CDP session** (`Network.enable` +
  `Network.getResponseBody` on `Network.loadingFinished`) — this is the fix
  for the body-eviction gotcha; it reliably yields the full SSE body.
- **Stateful context retained.** Same conversation (URL became
  `/c/<conversation_id>` after turn 1). Turn 1 stored two facts; turn 2 asked
  for them "without repeating" and got: *"Your name is Zephyrine, and your
  favorite number is 27."* → `STATEFUL_CONTEXT_RETAINED=True`. Confirms the
  stateful design: keep the conversation open and send only the new message.

Redacted SSE sample: `samples/sse_f_conversation.redacted.txt` (shows
`event: delta_encoding` / `data: "v1"`, then `resume_conversation_token`,
`input_message` echo, then assistant `event: delta` append ops).

## Working primitive (foundation for the proxy)

The reusable browser-backed send is: **UI-trigger + CDP SSE capture** (no need
to reimplement token minting):

1. `ctx = launch_persistent_context(PROFILE, headless=True)`; `page = ctx.pages[0]`.
2. `client = ctx.new_cdp_session(page); client.send("Network.enable")`; record
   `requestId` for URLs ending `/f/conversation`, fetch body on
   `loadingFinished`.
3. Send: focus `#prompt-textarea`, `keyboard.type(text)`, `keyboard.press("Enter")`.
4. Wait for the last `[data-message-author-role="assistant"]` text to stabilize
   (poll until unchanged ~3 s) — reliable "response complete" signal.
5. For a follow-up, just send again on the same page (same `conversation_id`).

## Notes toward the server

- A "response complete" signal that worked: assistant DOM text unchanged for 2
  consecutive polls. (Alternative: SSE `message_marker` `last_token` / message
  `status:"finished_successfully"`.)
- For OpenAI **streaming** output we'll parse the SSE `v1` delta ops
  (`o:add`/`append`, path `p`, value `v`) into `chat.completion.chunk`s rather
  than DOM-scraping; the DOM-stabilize method is the simple non-streaming path.
- All browser ops must be **serialized** (one send at a time per page) — a
  single CDP/page can't be driven concurrently.

## Pending / next

- [ ] Scaffold the OpenAI-compatible HTTP server around this primitive
      (`/v1/chat/completions` stateful → one ChatGPT conversation;
      `/v1/models` from `GET /backend-api/models`; real slugs only).
- [ ] Full `models` list capture (for `/v1/models`).
- [ ] SSE `v1` delta parser for token streaming.
