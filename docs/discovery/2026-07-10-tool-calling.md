# 2026-07-10 — Tool calling: native ChatGPT tools + emulated function calling

Two separate things share the word "tools" here, and they work very
differently:

1. **ChatGPT's own native tools** (web search / `web.run`, python, image gen).
   The backend runs these itself; they show up inside the `f/conversation` SSE
   stream. We don't *drive* them, we just observe/clean their output.
2. **OpenAI-style function calling** (`tools=[...]` → `tool_calls`). ChatGPT's
   web backend exposes **no client-facing function-calling API**, so this is
   **emulated with a prompt contract** and parsed back out of the model's text.

Both were validated end-to-end (direct client + the `pi` coding agent).

## Part 1 — Native tools (web search), as seen in the SSE stream

Capture: sent "search the web for the top Hacker News story…" through the live
session and dumped the raw `f/conversation` SSE. Key structural finding:

**Native-tool routing is by `author.role` + `recipient` + `channel`, NOT by
`content_type`.** In one search turn you see:

| message | author.role | content_type | recipient | channel | meaning |
|---|---|---|---|---|---|
| hidden setup | `system` | text (empty) | all / assistant | — | plumbing, `is_visually_hidden_from_conversation` |
| the search call | `assistant` | `code` | `web` | — | `search("…")` issued to the `web.run` tool |
| tool activity | `tool` (`web.run`) | text (empty) | all | — | `metadata.search_model_queries` holds the queries |
| the answer | `assistant` | `text` | `all` | `final` | the user-visible reply |

Consequences for the parser (`sse.py`):

- **Gate emission on `recipient in (None, "all")`.** The `code`/`recipient:"web"`
  search-query message and other tool-routed messages must not be emitted as
  content. (The `code` message also carries its payload in `content.text`, not
  `parts`, so it wasn't leaking, but the recipient gate is the correct general
  fix for any future native tool.)
- `url_moderation` is another stream `type` to ignore.
- Markers seen: `search_start`, `user_visible_token`, `final_channel_token`,
  `last_token`.

### The citation-token pollution (important)

The user-visible answer text is peppered with **private-use-area (PUA) marker
spans** that the web UI resolves against `content_references` metadata. Raw
example (repr):

```
**URL:** urlcdn.openai.com (PDF)https://cdn.openai.com
… front page. citeturn0search0
```

Format: `U+E200 <kind> [U+E202 <field>]* U+E201`, where
- `` = start, `` = field separator, `` = end;
- `kind` ∈ {`url`, `cite`, `genui`, …}. For `url` the fields are
  `<display>` then `<real_url>`; `cite` points at a `content_references`
  entry (type `grouped_webpages`) that carries the real URLs + a ready-made
  markdown `alt` link.

If passed through untouched these render to an API client as garbage like
`citeturn0search0` and mangled URLs. **Fix (`sse.py::_declutter`, streaming-safe):**
strip every `…` span; render `url` markers as `[display](real_url)`
markdown, drop `cite`/`genui`/etc. Buffer across chunk boundaries so a marker
split between two SSE deltas is still removed. Verified: the HN search answer
comes out clean, `[cdn.openai.com (PDF)](https://cdn.openai.com)`, no PUA.

**Note:** native search auto-triggers on `auto`/GPT-5; we don't have to toggle
anything. Dropping `cite` footnotes loses those specific source links (the
inline `url` links are kept) — acceptable for now; resolving `cite`→URL from
`content_references` inline is a possible future refinement.

## Part 2 — Emulated OpenAI function calling

ChatGPT web can't be handed arbitrary tool schemas, so `tools.py` emulates the
OpenAI contract:

- **Inject** (`build_preamble`): on the first turn of the ChatGPT conversation,
  send the caller's system prompt + a `# Tools` contract describing each tool
  (name / description / JSON-Schema params) and the output format: a fenced
  block tagged **`tool_call`** containing `{"name":…, "arguments":{…}}`.
- **Parse** (`parse_tool_calls`): pull the ```` ```tool_call ```` block back out
  and convert to OpenAI `tool_calls` (`finish_reason:"tool_calls"`). Fallbacks
  accept a ```` ```json ```` block or a bare top-level JSON object if it has the
  right shape. Any prose before the block becomes `content`.
- **Feed results back** (`format_tool_result`): a `role:"tool"` message is
  rendered as the next user turn: ``Result from tool `name` (call id):\n<content>``.
- **Streaming**: tool calls can't be recognized until the reply is complete, so
  a tools-enabled request is **buffered**, then emitted as a single
  `delta.tool_calls` chunk (`server.py`). Plain requests still true-stream.

### Stateful mapping had to be generalized

The old planner keyed continuation off *user* turns only; tool flows advance via
`assistant(tool_calls)` and `role:"tool"` messages too. `server.py::plan_turn`
now diffs a **signature of the whole `messages[]`**: if the previous signature
is a prefix of the current one it's a continuation (forward only the new
user/tool messages, formatting tool results); otherwise start a fresh ChatGPT
conversation with the preamble. Assistant messages are never echoed back (they
were ChatGPT's own output).

### Validation

- Direct client, non-stream: "how many .py files in src/chatgpt_proxy?" →
  model emitted `run_bash("find … | wc -l")` → we executed → fed `8` back →
  final answer "8". ✅
- Direct client, streaming: `get_weather` → correct
  `delta.tool_calls[{index,id,function:{name,arguments}}]`, `finish_reason:
  tool_calls`. ✅
- **`pi` coding agent** (real tools write/read/bash, `--tools write,read,bash`):
  created a file and read it back. ✅

### Finding: forbid multi-call batches (the read-before-write race)

With `pi`, the model first emitted a **parallel array** `[write(note.txt),
read(note.txt)]` in one reply. `pi` runs array calls concurrently, so `read`
raced ahead of `write` → `ENOENT`, and the model then wrongly concluded "tools
aren't working" and gave up (twice, across contract tweaks). Because this proxy
is inherently **serialized** (one browser, one conversation), the fix is to
mandate **exactly one tool call per reply** in the contract (parser still
tolerates arrays if a model emits them). After that change `pi` ran cleanly:
`write` → result → `read` → result → final answer. Also added firm language
that the tools are real ("never claim you cannot run them; never fabricate a
result") after an early run where the model refused outright.

## Files

- `tools.py` (new) — contract builder, result formatter, tool-call parser.
- `sse.py` — recipient gating + PUA citation `_declutter`/`finalize`.
- `server.py` — `plan_turn` (signature-diff planning) + buffered tool-call
  response path (stream + non-stream) + `tool_choice` handling.

## Operational gotcha (not a proxy bug)

Restarting the server with a CPU-spinning `while …; do :; done` wait wedged the
harness (killed the relaunch). To restart: SIGTERM the server and wait on the
PID without busy-looping (e.g. `tail --pid=<pid> -f /dev/null`) before launching
the next one, so the new browser doesn't collide with the old profile lock.

## Update — thinking models, the native sandbox, and a parser fix

Real-world test: `pi` building a small recursive-descent calculator
(`calc.py` + `test_calc.py`) through the proxy, using the thinking model
`gpt-5-4-t-mini`. What we learned:

1. **Unclosed `tool_call` fence (parser bug, fixed).** The thinking model emitted
   a valid opening ```` ```tool_call ```` + JSON and then **stopped without the
   closing ```` ``` ```` — literally honoring our "then stop" instruction. Old
   `_FENCE` required a closing fence, so `parse_tool_calls` missed it and returned
   the block as *content*; `pi` printed it and exited (0 files built). **Fix:**
   `parse_tool_calls` now salvages an unclosed fence via `_OPENFENCE` +
   `_first_json_object` (brace-balanced JSON extraction, ignoring braces in
   strings). After the fix the call parsed and `pi` continued statefully
   (`msgs=4`, roles `system,user,assistant,tool`).

2. **Native code-interpreter sandbox (the core blocker).** `gpt-5-4-t-mini` has
   ChatGPT's own python/`web.run`-style tools. Instead of (or on top of) our
   contract it **executes in ChatGPT's sandbox and/or hallucinates results.** A
   direct "use the bash tool to list files" returned a listing of ChatGPT's own
   container root — `/`, `.dockerenv`, `caas_toolbox`, `openai`. In the `pi`
   build it ran our `bash` once (real, empty cwd), then falsely asserted "both
   files already present in the root filesystem" and "`python /test_calc.py`
   passes", and finished **without ever calling `write`.** Nothing was built.

3. **Mitigations added (this session).** The contract now states the model has
   **no** private sandbox / code interpreter / python and that the listed tools
   are the ONLY way to act — on the user's *real* machine — plus "if you output
   results that didn't come from a `Result from tool` message you are
   hallucinating." It must **always close the fence**. The preamble is now framed
   as an explicit `# SYSTEM INSTRUCTIONS` block that opens with an
   **available-tools roster** ("exactly these N tools and no others: …"),
   delimited from a `# USER REQUEST`. These **reduced but did not eliminate** the
   thinking model's sandbox hallucination.

4. **Non-thinking `gpt-5-mini`.** On a direct simple request it emits a clean
   `write` tool_call under the new contract (`finish_reason:"tool_calls"`,
   verified). On the full `pi` build (pi's own large system prompt + a two-file
   task) it stalled with clarifying/meta prose in 2/2 runs and built nothing — a
   task/prompt-interaction issue, not a parser bug.

**Conclusion:** emulated tool calling is reliable for a single, simple tool step
but **not yet reliable for multi-step autonomous builds** with these web models;
the thinking model's native sandbox is the main obstacle. **Most promising real
fix (not yet done):** disable ChatGPT's native tools at the source by setting the
right field(s) in the `f/conversation` request body — we already rewrite that
body via CDP `Fetch` (see `_apply_overrides`). Candidates to probe:
`system_hints`, `conversation_mode`, or an explicit tools/`disabled_tools` field.

## Update 2 — deactivating Canvas/Code, prompt tuning, and the `InvalidRecipient` wall

The user turned off Canvas and Code in ChatGPT settings and we retried. Findings,
in order (each contract tweak fixed the previous failure and exposed the next —
classic whack-a-mole, so we stopped after establishing the root cause):

1. **Settings toggle didn't remove the sandbox.** The single-shot "list files
   with the bash tool" probe *still* returned ChatGPT's container root
   (`.dockerenv`, `caas_toolbox`, `openai`). Deactivating Canvas/Code did not
   disable the code interpreter the models reach for.
2. **"This is a workaround" framing** (added at the user's suggestion, which they'd
   found works): the contract now says the native code/canvas tools are off *on
   purpose*, the `tool_call` blocks are a tested harness that runs on the user's
   real machine, and the model must not ask permission or claim the tools are
   down. This stopped `gpt-5-mini` from balking.
3. **"Never paste code inline" clause:** next, `gpt-5-mini` dumped the file as a
   ```` ```python ```` block instead of calling `write`. Added: to create/change a
   file you MUST use the write/edit tool; reply text is discarded, not saved.
   After this it emitted a real `write` tool_call.
4. **Malformed large tool JSON (fixed):** that `write` call had the full, correct
   file content but a broken envelope — missing the final `}` and a bogus
   `\end_tool_call` closer instead of ```` ``` ````. Added `_salvage_call_json`
   (strip junk closers; `raw_decode` to ignore trailing data; retry with a few
   appended braces / a closing quote). Verified it recovers the real 2.4 KB
   `write` call.
5. **`InvalidRecipient` — the architectural wall.** On a later run the model
   produced **no** text fence at all and reported that "the `write` tool is
   repeatedly returning an `InvalidRecipient` error." That is a **ChatGPT backend
   error**: the model tried to invoke `write` through ChatGPT's *native*
   recipient-based tool channel (the same mechanism as `web.run`/`python`), and
   the backend rejected our unknown tool name. It's **non-deterministic** whether
   a model uses our text contract or the native channel.

**Net:** `gpt-5-mini` (no sandbox) is closer than the thinking model, and the
prompt + parser fixes are real improvements (balk → inline code → real
tool_call), but reliability is capped by the models preferring ChatGPT's native
tool channel — which either runs in ChatGPT's sandbox or 400s our tool names
(`InvalidRecipient`). Prompt text can't fully win against that.

**Best lead now (supersedes the request-body idea above):** stop fighting the
native channel and *use* it. When a model routes a tool call natively, it shows
up in the `f/conversation` SSE as an `assistant` message with `recipient` = the
tool name (cf. web search's `recipient:"web"`, which `sse.py` already reads).
Intercept those: map a native tool-call attempt (recipient == a declared tool,
args in `content`) straight to OpenAI `tool_calls`. Needs a capture of the SSE at
the moment of an `InvalidRecipient` attempt to confirm the args are present in
the stream before the backend rejects them.

## Update 3 — native-channel interception WORKS (the real fix)

Captured the SSE during native tool calls (added an env-gated raw dump,
`CHATGPT_PROXY_DUMP_SSE=<file>`, in `browser.py::_feed`). Result: when a model
calls a client tool natively, the **arguments stream as complete, valid JSON**
in the message body — and (this run) with **no `InvalidRecipient`** at all:

```
recipient="write"  ->  {"path": "calc.py", "content": "import sys\n..."}
recipient="write"  ->  {"path": "test_calc.py", "content": "from calc import ..."}
recipient="bash"   ->  {"command": "python test_calc.py"}
```

Shape details that mattered:
- The message is `author.role:"assistant"`, `recipient:"<toolname>"`. Its args
  arrive **either** as `content_type:"code"` (in `content.text`) **or**
  `content_type:"text"` (in `content.parts`) — non-deterministic — so we capture
  the text **regardless of content_type**.
- Sometimes the model routes to a recipient it literally names `tool_call`
  (confusing our fence tag), wrapping a contract-shaped `{name, arguments}`
  object as the body. Handle that by normalizing the wrapped call.
- ChatGPT's own native tools (`web`, `python`) also use non-`all` recipients, so
  we keep only calls whose recipient is a **client-declared** tool name.

Implementation:
- `sse.py`: `_emit` sends any non-`all` recipient text to `_native_append`,
  accumulating per message id into `parser.native_calls`; `finalize` surfaces
  each as a `("tool_call", {name, arguments})` event.
- `tools.py::native_to_openai(native, allowed_names)`: filters to client tools,
  parses args (lenient salvage), and also unwraps a `tool_call`-recipient
  contract object. `tool_names(tools)` gives the allow-set.
- `server.py`: the buffered tools branch collects `tool_call` events and
  **prefers** the native call (first one, to stay serialized) over the text
  fence.

**Validation (curl, fast):** `gpt-5-mini` on the calc task returned a real
`write` tool_call on **5 of 6** attempts (native `write`/`bash` → OpenAI
`tool_calls`); the one miss was a browser composer-click timeout, not the model.

**Model matters — and newer isn't better here.** `gpt-5-5-mini` (`reasoning_type
none`, `enabled_tools:[tools,tools2,search,canvas,image_gen]`) did **not** call
tools at all: every message was `recipient:"all"` and it **hallucinated**
"Implemented calc.py and test_calc.py ... Result: ALL TESTS PASSED" against an
empty dir. So `gpt-5-mini` + native interception is the working combo; the newer
mini fabricates completion instead of calling tools.
