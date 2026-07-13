# Emulated "thinking mode" for chatgpt.com (two-conversation reason→act)

**Date:** 2026-07-12 · **Tag:** [chatgpt] · **Status:** implemented (pi integration)

## Problem

chatgpt.com's web models, driven through the `webllm-agent` emulated agent mode,
reason poorly when just asked to "think then act" in a single reply: they answer
fast and over-confidently, skip edge cases, and sometimes hand back prose ("looks
correct to me") instead of actually verifying. We want real deliberation without
any model-weights/API control — only a chat surface.

## Idea

Split each pi agent turn into **two independent chats** over the gateway:

1. **Reasoning chat** (`webllm_proxy/prompts/chatgpt_think.md`): given the task +
   the transcript so far, reply with ONLY a `<thinking>…</thinking>` block. The
   prompt forces the deliberation techniques we want: restate the goal, reason
   only from evidence already seen (never invent results), self-question, hold
   2–3 competing hypotheses, enumerate edge cases / failure modes, and then
   **challenge the conclusion once** ("Wait — is that actually verified, or am I
   guessing? What did I skip?") before choosing the single most informative next
   step.
2. **Action chat** (`prompts/chatgpt_agent.md`, the existing tag contract): given
   the same transcript **plus** the produced `<thinking>`, emit exactly one action
   tag (or a verified final note).

If the action chat returns prose instead of an action, that prose is folded back
into the reasoning chat with a reconsideration nudge and reasoned about again
(capped at `maxReconsider`, default 1) before it is accepted as the final answer.
This is the "prose → back to thinking" loop the design calls for; it turns a lazy
"it's probably fine" into either evidence or another action.

The `<thinking>` is surfaced to pi as a **native reasoning block** (not prose):
`agentThinking.runThinkingTurn` emits `thinking_start` / `thinking_delta` /
`thinking_end` events and appends a `ThinkingContent` to the assistant message,
so it renders and collapses exactly like a real thinking model. Reconsideration
passes append more deltas to the same single block.

## The constraint that shaped the design (important)

You cannot do this as two *threaded* web conversations. The proxy keeps **one
`ConversationPlanner` per browser session** (`webllm_proxy/application/chat.py`),
and it only *continues* an existing web chat when the incoming `messages[]` is a
pure append of the previous list; otherwise it re-primes with just `system + last
user turn` and starts a fresh chat (navigating the page,
`providers/chatgpt/__init__.py`). Two interleaved conversations (reasoning vs
action) always diverge, so **every call is a fresh chat anyway**.

So we lean into it: **each reasoning/action call is ONE self-contained user
message** — the relevant prompt with `<<PROJECT_TREE>>` filled in, the full
rendered tag transcript of the turn so far, and the specific ask. Our provider
sends no `system` role and no tools, and `build_preamble` returns `""` for that
case (`strategies/tool_calling/agentclip.py`), so the single message passes
straight through as the fresh chat's body. No reliance on web-side threading;
"two conversations" is realized as two independent fresh chats per step.

One deliberate consequence: prior `ThinkingContent` is **not** replayed into the
transcript (`renderTranscript` skips it). Reasoning is regenerated each turn from
the concrete `<result>` outputs, so earlier maybe-wrong thinking is never fed
back as established fact (this also avoids the "hollow restatement" failure mode
where a model just re-asserts its previous conclusion more confidently).

## Where it lives

- `integrations/pi/src/agentThinking.ts` — pure helpers (`extractThinking`,
  `renderTranscript`, `buildThinkingMessage`, `buildActionMessage`,
  `reconsiderNudge`) + the `runThinkingTurn` engine. Unit-tested in
  `test/agentThinking.test.ts` (reason→act, prose→reconsider→act, prose→cap→final,
  empty-thinking→no block, `maxReconsider=0`, abort).
- `integrations/pi/src/agentStream.ts` — branches to `runThinkingTurn` when
  thinking is on (binds `callModel` to a single-message gateway POST).
- `integrations/pi/src/agentSettings.ts` — second toggle axis, **default on**:
  setting `webllm.chatgptThinkingMode`, env `WEBLLM_CHATGPT_THINKING`.
- `integrations/pi/extensions/chatgpt-agent.ts` — reads the toggle, sets the model
  `reasoning` flag + "(thinking agent)" label, adds `/webllm-agent thinking on|off`
  (re-registers live).
- Prompt: `webllm_proxy/prompts/chatgpt_think.md` (source) + bundled copy
  `integrations/pi/prompts/chatgpt_think.md` (kept in sync by hand, like
  `chatgpt_agent.md`).

## Tradeoffs

- **Latency / quota:** thinking = at least two fresh web chats per turn, each a
  page nav + bot-check. Noticeably slower and heavier on web quota; toggleable,
  reconsideration capped at 1. A future proxy-side multi-conversation channel
  could avoid the re-nav (out of scope).
- `gpt-5-5` has shown upstream empty-completion flakiness; verify with a healthy
  model (`gpt-5-mini` / `gpt-5`).

## Live verification

Target: a subtle root-cause bug that only careful edge-case reasoning surfaces,
planted in an isolated module under `docs/discovery/project_test/` (keeping
`main.py` a clean baseline). See `is_prime_buggy.py` — `while i * i < n` instead
of `<= n`, so perfect squares of primes (9, 25, 49, …) are misreported as prime;
`test_is_prime_buggy.py` fails on exactly those.

Run (gateway `:5100` + `serve --provider chatgpt` `:5102` up), from inside
`docs/discovery/project_test/`:

```
WEBLLM_CHATGPT_AGENT=1 pi \
  -e integrations/pi/extensions/chatgpt-agent.ts \
  --model 'webllm-agent/chatgpt__gpt-5-mini' --no-session \
  -p "The tests in test_is_prime_buggy.py are failing. Investigate, find and fix
      the root-cause bug in the source, then re-run the tests to confirm."
```

### Result — live, 2026-07-12, `gpt-5-mini`

It worked end-to-end. The agent diagnosed the exact edge case and fixed the root
cause; all 4 tests passed. pi's final answer:

> All failing tests in `test_is_prime_buggy.py` are now passing. The root-cause
> bug was the loop boundary in `is_prime`: changing `while i * i < n` to
> `while i * i <= n` correctly handles perfect squares of primes. The fix is
> verified by the [test] results, so the issue is resolved.

The proxy log confirmed the design running as intended — **21 gateway calls over
~10 turns, every one `system_chars=0 tools=0 new_conv=True` and a single
self-contained user message** (so each is an independent fresh chat, never the
proxy's threaded continuation). Per turn the sizes alternate reasoning (smaller,
~3.5k→10k chars) then action (larger, ~8k→17.5k chars), both growing as the
transcript accumulates real `<result>` output. No `system`/tools were ever sent,
so `build_preamble` passed each message straight through as the chat body, as
designed.

(`is_prime_buggy.py` was restored to its buggy state afterwards so it stays a
re-runnable demo target.)

## Update — live hardening (2026-07-12, later)

A run driven interactively against `gpt-5-5` surfaced three real bugs; all fixed
and re-verified live:

1. **The model refuses to expose "reasoning".** Asking chatgpt.com's models
   (esp. `gpt-5-5`) for their `<thinking>`/"reasoning"/"private analysis" trips a
   guardrail: *"I can't provide my private reasoning or internal thought process,
   even if requested in a specific format."* Rewording alone didn't help while the
   ask was still "your thinking". The fix that works: ask for a **`<plan>` of
   attack** (planning framing, no "reasoning/thinking/internal" words). The model
   then happily writes exactly the same evidence-based, self-questioning content —
   with no disclaimer. `chatgpt_think.md` now asks for `<plan>`; `extractThinking`
   accepts `<plan>`/`<analysis>`/`<thinking>`. It's still surfaced as a native pi
   *thinking* block. (Verified: `gpt-5-5` returns a clean `<plan>`, 0 refusals over
   a full run.)
2. **`find`/`grep` aren't pi's default tools.** pi's default-active set is exactly
   **read / bash / edit / write** (`pi --help`); `find`/`grep`/`ls`/`ripgrep` are
   defined but not active, so `<find>` mapped to a `find` tool → "Tool find not
   found". Fix: `actionToToolCall` routes `<find>`/`<search>` through **`bash`**
   (`find`/`grep`), and `chatgpt_agent.md` tells the model to use `<bash>` for
   finding/searching. (Separately: reaching a chatgpt model via the `/chatgpt`
   pass-through calls `pi.setActiveTools([])`, disabling ALL local tools for the
   session — so use `/model webllm-agent/…`, not `/chatgpt`, to keep tools.)
3. **Path prefixing.** `buildProjectTree` labelled the root with the cwd's dir
   name, so the model read `calc_challenge/SPEC.md` when cwd already *was*
   `calc_challenge`. Fix: label the root `./` and note in the prompt that paths
   are relative to it. (Verified: a smoke run used `mymath.py`, never the dir
   prefix.)
