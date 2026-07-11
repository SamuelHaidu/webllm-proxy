# Refactor progress tracker (clean-arch + research tool)

> **Substitute for an ai-memory wiki page.** The `mcp__ai-memory__*` tools were
> not present in the assistant's callable tool set in the sessions that did this
> work (only `Bash`/`Read`/`Edit`/`Write`/`AskUserQuestion` were available — check
> again next session, this may just be a tool-loading gap). Until they're
> confirmed working, **this file is the durable handoff/status doc** — read it
> first on any clean-context resume, alongside the full spec at
> `/home/samuel.haidu/.claude/plans/utility-research-tool-tingly-journal.md`
> (decisions, target file tree, phase checklists — this file does not repeat
> that content, only status + findings). Branch: `refactor/clean-arch`
> (created from `go-cdp-version`).
>
> Update this file **as you go**, not just at the end: tick items, append dated
> entries under "Findings / deviations from plan" for anything surprising,
> and update "Where to resume" at the top before ending a session.

## Where to resume

_(keep this section current — overwrite, don't append)_

- **Current phase:** Phase A complete and verified. Starting Phase B (research feature).
- **Last completed step:** Phase A gate passed: `uv run poe check` green;
  parity smoke on both providers after the full modularization (models,
  streaming chat, tool round-trip) — chatgpt live end-to-end (including the
  critical lock-deadlock fix, see Findings), databricks via static review +
  a fake-session route-registration smoke (its login session is currently
  stale, see Blocking issues).
- **Next step:** Phase B — `domain/research.py` + `research/jobstore/memory.py`
  (B1), the emulated backend (B2, ships first — must work on this free-tier
  account), the Deep Research backend (B3 — likely an honest stub, see plan
  §2 "Account is FREE plan"), the registry (B4), `application/research.py` +
  `ResearchScheduler` (B5), and the REST API + CLI (B6).
- **Blocking issues:** none. (Non-blocking: the databricks profile's session
  is logged out — pre-existing, unrelated to this refactor; needs
  `webllm-proxy login --provider databricks` headed, not attempted here since
  it needs a display and shouldn't be done unattended. Re-run the databricks
  live smoke test once that session is valid again.)

## Phase checklist

(Mirrors plan §8. `[x]` = done and verified, `[~]` = in progress, `[ ]` = not started.)

### Phase 0 — Toolchain, flat layout, cross-platform infra ✅ DONE
- [x] Move `src/webllm_proxy/` → `webllm_proxy/`, delete `src/`, fix pyproject + test imports
- [x] `pyproject.toml`: deps (toolz, psutil, platformdirs) + dev group (ruff, ty, pytest, poethepoet) + ruff/ty/poe config
- [x] `infra/{env,logging,redaction}.py`
- [x] `transport/process.py` (psutil-based, cross-platform)
- [x] `cli.py` split out of `__main__.py`; signal handling platform-guarded
- [x] Route the two `/tmp/*_last_request.json` dumps through `infra.logging.dump_exchange`
- [x] Gate: `uv run poe check` green; `webllm-proxy serve` boots both providers

### Phase A — Modularize onto clean arch ✅ DONE
- [x] A1 Prompts → markdown (`prompts/loader.py` + 5 `.md` files)
- [x] A2 Domain + ports (`domain/ports.py`, `domain/conversation.py`)
- [x] A3 Transport (`transport/browser.py` + per-job timeout)
- [x] A4 Tool strategies (`strategies/tool_calling/{agentclip,native_channel}.py`)
- [x] A5 Wire + thin http + thin providers (`wire/`, `application/chat.py`, `http/*`, `server.build_app`)
- [x] Gate: parity smoke both providers (models, streaming chat, tool round-trip) —
      chatgpt live; databricks static + fake-session route smoke (session logged out)

### Phase B — Research feature
- [ ] B1 Domain + store (`domain/research.py`, `research/jobstore/memory.py`)
- [ ] B2 Emulated backend (ships first)
- [ ] B3 Deep Research backend
- [ ] B4 Registry (`research/backends/resolve_backend`)
- [ ] B5 Application (`application/research.py`, `ResearchScheduler`)
- [ ] B6 REST API (`http/research_routes.py`) + CLI `research` subcommand

### Phase C — Discovery, docs, release
- [ ] Deep-research discovery session + doc
- [ ] `scripts/build_offline_bundle.py` + `install_offline.{sh,ps1}`
- [ ] README: corporate install + architecture map
- [ ] `uv run poe release`

## Findings / deviations from plan

_(dated, newest first — append, don't rewrite history)_

- **2026-07-11 — CRITICAL (found + fixed via live testing, not caught by unit
  tests or `ty`):** the first cut of the "serialized_browser" helper
  (`http/health.py`, then called `stream_with_lock`) called `lock.acquire()`
  itself, but every call site had ALREADY acquired that same `threading.Lock`
  right before `session.submit(...)` (matching the pre-refactor code). Since
  `threading.Lock` isn't reentrant, the streaming chat-completions path
  deadlocked the request thread on its own re-acquire — and because the lock
  was never released, **every subsequent request to chatgpt's
  `/v1/chat/completions` piled up behind it forever** (each blocks at its own
  first `lock.acquire()`, before ever calling `session.submit()`). Import
  smoke tests, the route-registration smoke test, and all 55 unit tests passed
  clean; only a real end-to-end `curl --max-time N` against a live streaming
  request surfaced it (client hung, then `--max-time` gave `http_code=000`).
  **Fixed:** renamed to `release_lock_when_done(lock, generator)` — it only
  releases (in a `finally`) on an already-held lock, never re-acquires;
  regression tests added in `tests/test_health_helpers.py` (construct a
  pre-acquired `Lock`, assert it's released after the wrapped generator is
  exhausted / raises). **Lesson for later phases (and for Phase B's
  `ResearchScheduler`, which will have its own locking around the shared
  browser): locking helpers must be verified with a live, multi-request
  smoke test, not just unit tests + import checks** — a deadlock is invisible
  to both of those. Re-verified after the fix: chatgpt live end-to-end on
  every path (models, non-stream chat, streaming chat, non-stream tool call,
  streaming tool call, tool-result continuation), including two back-to-back
  streaming requests to confirm the lock actually releases.
- **2026-07-11 — Deliberate scope deviations from the plan draft** (all
  reasoned through during implementation, not oversights):
  - `domain/conversation.py` ships **only `ChatTurn`**, not the originally-
    sketched `Message`/`ToolCall`/`ToolResult` too. Those would have meant
    parsing the client's raw OpenAI JSON into a dataclass just to read a
    handful of fields already reachable via `.get(...)`, and re-serializing
    back to the identical JSON on the way out — exactly the DTO/mapper
    duplication the user explicitly said to avoid. `ChatTurn` earns its place
    (an actual internal concept, not a wire-JSON copy); the rest didn't.
  - `http/openai_routes.py` and `http/anthropic_routes.py` keep real, "final"
    complexity (see the pyproject per-file-ignores) rather than being forced
    down to `max-complexity=8`: they house the streaming + tool-calling +
    single-browser-locking request path, and by this point everything
    reasonably extractable already had been (`wire/`, `application/chat.py`,
    `strategies/tool_calling/`, `providers/databricks/llmproxy.py`). Further
    fragmenting the request flow across more functions would trade a linear,
    readable (and, per the finding above, locking-correctness-sensitive) flow
    for indirection, for no real benefit.
  - Kept per-provider HTTP registration functions (`register_chatgpt`,
    `register_databricks`, `register_databricks_openai`) rather than
    inventing a shared abstraction across providers that speak the same wire
    protocol but have unrelated backing logic (chatgpt: stateful + emulated
    tools; databricks: stateless near-passthrough to a different upstream).
    Grouped by **wire protocol family** (`http/openai_routes.py` vs.
    `http/anthropic_routes.py`), which is what the plan's file names actually
    imply, not by an artificial shared interface.
  - `providers/chatgpt/__init__.py` and `providers/databricks/__init__.py`'s
    `register_routes()` import their `http/*_routes` registration function
    **lazily** (inside the method), not at module level. `http/openai_routes.py`
    needs both providers' `config` (it hosts databricks' Azure channel too), so
    an eager import from inside `chatgpt/__init__.py` created a real circular
    import (`chatgpt.__init__` → `http.openai_routes` → `databricks.__init__`
    → `http.openai_routes`, partially initialized, missing the not-yet-defined
    `register_databricks_openai`). Lazy import breaks the cycle cleanly and
    matches the codebase's own pre-existing pattern (`providers/__init__.py`'s
    lazy provider imports, same rationale). New `PLC0415` per-file-ignores
    added for both provider `__init__.py` files, same justification as the
    existing `cli.py`/`providers/__init__.py` entries.
- **2026-07-11** — Phase 0 lint/type cleanup surfaced one real (pre-existing) bug:
  `chatgpt/sse.py`'s `StreamAccumulator` never actually inherited from the
  `Accumulator` ABC (duck-typed only, despite its docstring claiming to satisfy
  the interface) -- `ty` caught it as an `invalid-return-type` on
  `make_accumulator`. Fixed by making it a real subclass. `PassthroughAccumulator`
  was already correct; only the ChatGPT one had drifted. Worth remembering: `ty`
  earns its keep on exactly this kind of silent contract drift.
- **2026-07-11** — `ty`'s CDP/browser-boundary noise (6 `unresolved-attribute`
  errors on `self._page`/`_ctx`/`_client`) was **not** a few-`ty:ignore` situation
  in practice -- the real fix was declaring those three attributes `: Any` in
  `BrowserSession.__init__` (matching `cloakbrowser`'s own `Any`-typed returns)
  instead of leaving them inferred as `None`. Zero suppression comments needed
  after that; removed the speculative (and wrongly-named) `[tool.ty.rules]`
  override from pyproject that Phase 0's plan draft had pre-emptively added.
- **2026-07-11** — Ran a real (not just import-level) Phase 0 regression smoke
  per plan §10: `webllm-proxy serve --provider chatgpt` against the existing
  logged-in profile -- booted, authenticated, `/health` and a live `/v1/models`
  call all succeeded, clean SIGTERM shutdown, zero orphan CloakBrowser processes
  (verified via the new psutil-based `kill_profile_chrome`, matching the old
  behavior). `--provider databricks` correctly surfaced "Not logged in" (that
  profile's session is currently stale/expired -- pre-existing, not caused by
  this refactor) and exited cleanly with 0 orphans either way. Confirms the
  flat-layout + infra/transport split didn't regress either provider.
- **2026-07-11** — Confirmed (again) `mcp__ai-memory__*` and a `ToolSearch`
  meta-tool are **not** in the callable function schema this session (only
  Bash/Read/Edit/Write/AskUserQuestion). Same gap as the planning session. Using
  this file instead per the note above.
- **2026-07-11** — Pre-existing uncommitted changes on `go-cdp-version` at
  refactor start (unrelated to this work, left untouched): `docs/discovery/README.md`
  and `scripts/dbx_models_probe.py` modified, plus a new untracked
  `docs/discovery/2026-07-11-databricks-native-tools-and-models.md`. Carried
  over onto `refactor/clean-arch` (branch was cut from the dirty tree) — do not
  stage/commit these as part of refactor commits; they belong to other work.
- **2026-07-11** — `ty`/`ruff`/`poe` are not installed/on PATH yet (only `uv`
  itself); confirmed via `which`. They'll be added to `[dependency-groups] dev`
  and pulled by `uv sync` in the pyproject step.
- **2026-07-11** — Full recon of every source/test file completed (pyproject,
  `__main__.py`, `server.py`, `providers/__init__.py`, `providers/base.py`,
  `core/{__init__,env,process,browser}.py`, both providers' `__init__.py` +
  `config.py` + `routes.py`, `chatgpt/{sse,tools}.py`, all 3 test files,
  README, `.gitignore`, `.env`). No surprises vs. the plan's file-by-file
  mapping (§3) — the plan was written from accurate memory of these files.

## Notes for a from-scratch session

If you're picking this up cold: read the plan file completely first (it's
self-contained), then this file's "Where to resume" + "Findings" sections,
then `git log --oneline` + `git diff go-cdp-version...refactor/clean-arch --stat`
to see what's actually landed vs. the checklist above (the checklist is
maintained by hand and could lag).
