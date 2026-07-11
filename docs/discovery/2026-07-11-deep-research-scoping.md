# Deep Research: scoping, not triggering (why a live discovery session was skipped)

**[chatgpt]** Written during the clean-architecture refactor (see
`docs/refactor/PROGRESS.md`), while building the research-job feature
(`research/backends/`). This is a scoping note, not a reverse-engineering
result: it explains why `research/backends/deep_research.py` ships as a
documented stub instead of a working trigger, so a future session doesn't
have to re-derive the reasoning.

## The question

Can this proxy trigger ChatGPT's **Deep Research** mode (extended, multi-step
web research the ChatGPT UI runs when you pick that mode) over the same
browser-backed `f/conversation` transport everything else uses, instead of
emulating research with a plain chat turn + a research-style prompt?

## Why this wasn't investigated live this session

1. **The account behind this proxy is confirmed free-tier.** `docs/discovery/2026-07-10-tool-calling.md`
   Update 5 already established `plan_type: "free"` from a live
   `/backend-api/models`-adjacent capture, while building the tool-calling
   emulation. Nothing about the account changed between that session and this
   one (same persisted login profile).
2. **Deep Research has historically been a paid-tier-gated ChatGPT feature**
   (Plus/Pro/Team/Enterprise), not available on the free tier. This is a
   product-tier fact, not something this proxy's transport can work around --
   no request-body trick makes a backend honor a mode the account's
   entitlements don't include.
3. Given (1) and (2), a live discovery session (driving the authenticated
   browser, toggling Deep Research on in the real chatgpt.com UI, capturing
   the outgoing `f/conversation` body via the existing CDP `Fetch` rewrite
   hook to find the trigger field) would very likely have found **no Deep
   Research option to toggle at all** on this account -- spending a session
   on it now would mostly validate a fact already known, not add capability.
4. Per this project's discovery workflow (check existing findings first,
   don't re-derive) and the standing "don't guess APIs" rule: rather than
   invent a plausible-looking trigger field (candidates that come to mind --
   `system_hints`, `conversation_mode`, `enabled_tools`/`disabled_tools` on
   the `f/conversation` body -- are exactly that, guesses, unverified against
   any real capture), the honest choice was to ship the seam
   (`domain.ports.ResearchBackend`, `research.backends.resolve_backend`)
   without a fabricated implementation behind it.

## What actually shipped instead

`research/backends/emulated.py` -- a plain ChatGPT chat turn, prompted (see
`prompts/research_emulated.md` + `prompts/research_report.md`) to do
thorough native web search and answer in a structured markdown report. No
Deep Research mode needed: this is just a chat turn like any other, so it
works on this (and any) account today. **Live-verified** (see
`docs/refactor/PROGRESS.md`, 2026-07-11): a real request produced a report
citing real, non-fabricated sources (`toolz.readthedocs.io`, the PyPI page)
in about 10 seconds.

## The concrete next step, if this is ever revisited

Needs an account with Deep Research entitled (Plus tier or above), then:

1. Open a headed, authenticated browser to chatgpt.com on that account, CDP
   `Fetch`-intercepting `*/backend-api/f/conversation*` (the existing hook in
   `providers/chatgpt/__init__.py`, `on_fetch_paused`) -- but *reading and
   diffing* the outgoing `postData`, which today's code only *rewrites*, never
   *inspects*. This is the not-yet-done capture flagged repeatedly in earlier
   sessions (`docs/discovery/2026-07-10-tool-calling.md`,
   `docs/discovery/2026-07-10-backend-api-capture.md`).
2. Send one message with Deep Research OFF, then one with it ON (same
   message), and diff the two captured bodies -- the field(s) that differ are
   the trigger.
3. Wire that into `DeepResearchBackend.run()` (mirroring
   `ChatGptProvider.on_fetch_paused`'s existing body-rewrite pattern) and flip
   `available()` to check the account's entitlement (likely visible in the
   `/backend-api/models` response's plan/feature flags, same place
   `plan_type` was found) rather than hardcoding `False`.
4. Extend `providers/chatgpt/sse.py`'s `V1DeltaParser` for whatever new
   message/event shapes Deep Research's multi-step research process streams
   (step/progress events, intermediate search results, etc. -- unknown until
   captured).

Until then, `resolve_backend()` keeps using `emulated`, which is the
guaranteed-working path regardless of tier.
