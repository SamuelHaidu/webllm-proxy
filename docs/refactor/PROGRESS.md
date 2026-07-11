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

- **Current phase:** ALL PHASES DONE (0, A, B, C). The plan at
  `/home/samuel.haidu/.claude/plans/utility-research-tool-tingly-journal.md`
  is fully implemented and verified.
- **Last completed step:** `uv run poe release` — green end to end: fmt/lint
  (ruff)/typecheck (ty)/test (64 passed), then `uv build` produced both
  `dist/webllm_proxy-0.2.0.tar.gz` and `dist/webllm_proxy-0.2.0-py3-none-any.whl`
  cleanly (`dist/` is gitignored, nothing to commit there). **Did not run
  `uv publish`** — publishing was never requested/authorized, and `release`'s
  own poe-task definition deliberately excludes it (`release = ["check",
  "build"]`; `publish` is a separate, manual-only task).
- **Next step (if anyone continues this work):** nothing required by the
  plan is outstanding. Two carried-over, non-blocking loose ends, both
  pre-existing and unrelated to this refactor:
  1. The databricks profile's browser session is logged out (needs a headed
     `webllm-proxy login --provider databricks` to re-verify databricks live
     end-to-end — not done in this effort, see Blocking issues below).
  2. Two stash entries from mid-refactor commit-splitting are still in
     `git stash list` (`wip: phase B/C ...`, `wip: phase C ...`) — fully
     redundant/superseded, safe to `git stash clear` whenever convenient.
  Otherwise: pick up new work directly from the plan's already-completed
  state, or start a fresh feature on top of the finished architecture.
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

### Phase B — Research feature ✅ DONE
- [x] B1 Domain + store (`domain/research.py`, `research/jobstore/memory.py`)
- [x] B2 Emulated backend (ships first) — **live-verified, works, cites real sources**
- [x] B3 Deep Research backend — honest stub (`available()` hardcoded False); see Findings
- [x] B4 Registry (`research/backends/resolve_backend`)
- [x] B5 Application (`application/research.py`, `ResearchScheduler`)
- [x] B6 REST API (`http/research_routes.py`) + CLI `research` subcommand — **live-verified**

### Phase C — Discovery, docs, release ✅ DONE
- [x] Deep-research discovery session + doc (scoping note, not a live trigger
      capture — see Phase B Findings; `docs/discovery/2026-07-11-deep-research-scoping.md`)
- [x] `scripts/build_offline_bundle.py` + `install_offline.{sh,ps1}` — live-verified
- [x] README: corporate install + architecture map
- [x] `uv run poe release` — check green + `uv build` produced sdist + wheel

## Findings / deviations from plan

_(dated, newest first — append, don't rewrite history)_

- **2026-07-11 — Plan complete.** `uv run poe release` (check + `uv build`)
  is green: ruff format/lint clean, `ty check webllm_proxy` clean, 64 tests
  passed, both `dist/webllm_proxy-0.2.0.tar.gz` and the wheel built
  successfully. Every checklist item in every phase (0, A, B, C) above is
  `[x]`. `uv publish` was deliberately not run (never authorized; not part
  of the `release` poe task by design). This is the closing entry for the
  modularization + research-tool + hardening effort described in
  `/home/samuel.haidu/.claude/plans/utility-research-tool-tingly-journal.md`;
  see the two non-blocking loose ends noted in "Where to resume" for
  whoever picks this up next (stale databricks login session; two
  redundant `git stash` entries safe to clear).

- **2026-07-11 — `scripts/build_offline_bundle.py`: two real bugs, both only
  visible by actually running the script, not by reading it:**
  1. `uv export --format requirements.txt --no-dev --no-hashes` (no further
     flags) includes **this package itself** as a local-path requirement
     alongside its real dependencies. Handing that to `pip download` makes
     pip try to build/archive this project into the same `wheels/` dir where
     `uv build --wheel` had *just* placed its own wheel a moment earlier —
     pip's interactive conflict prompt (`(i)gnore/(w)ipe/(b)ackup/(a)bort`)
     then hangs forever non-interactively (`EOFError` on `input()`). Fixed
     with `uv export --no-emit-project` (verified via `uv export --help`,
     not guessed) so the export only lists real runtime deps. Also made
     `main()` `shutil.rmtree(OUT, ignore_errors=True)` before rebuilding, so a
     stale wheel from a *previous* run of this same script can never trigger
     the same collision again.
  2. `_bundle_cloakbrowser_binary()` named the archive after
     `cache_dir.name` (e.g. `chromium-146.0.7680.177.5.tar.gz`), but both
     `install_offline.sh` and `.ps1` glob for `cloakbrowser-*.tar.gz` — a
     silent no-op on the target machine (install would run pip only, print
     "no bundle found", never extract the real one). Fixed by prefixing the
     archive name: `cloakbrowser-{cache_dir.name}.tar.gz`. Confirmed via
     `binary_info()`'s real `cache_dir`/`binary_path` fields
     (`~/.cloakbrowser/chromium-.../chrome`) and cloakbrowser's own
     `config.get_cache_dir()`/`download.py` source that extracting the
     archive back into `~/.cloakbrowser/` (what the install scripts do)
     recreates the exact cache layout cloakbrowser's own lookup already
     checks — so **no `CLOAKBROWSER_BINARY_PATH` override is needed** on the
     target machine, just the extraction. (`CLOAKBROWSER_BINARY_PATH` itself
     was confirmed, by reading `cloakbrowser/config.py`/`download.py`, to
     want the `chrome` binary path directly, not a directory — relevant only
     if a user pre-stages a binary manually instead of using this bundle.)
  Live-verified end-to-end twice after both fixes: `uv run poe bundle`
  produces `dist/offline/{wheels/ (26 dep wheels + own wheel),
  cloakbrowser-chromium-146.0.7680.177.5.tar.gz, install_offline.sh, .ps1,
  requirements.txt}`; `tar -tzf` on the archive confirmed the top-level dir
  matches the real cache dir name. `uv run poe check` still green (64 tests,
  ruff+ty clean) with the script in the tree. Also added `UP015` to
  `scripts/*`'s ruff per-file-ignores (an unrelated pre-existing script,
  `har_explore.py`, trips it; the ignore list is already documented as a
  deliberately looser bar for one-off `scripts/*` tools, this just extends
  it -- see that entry's own comment in pyproject.toml).
- **2026-07-11 — Commit hygiene: split the already-completed Phase 0/A/B
  work (all done in one continuous, uncommitted pass across earlier
  sessions) into two logical commits matching the plan's own phase
  boundaries** rather than one large commit, per the user's ask for
  incremental logical commits: (1) flat layout + clean architecture + strict
  toolchain (Phases 0+A), (2) the research feature (Phase B). Two files
  needed a temporary hand-trim to make commit (1) buildable/gate-clean on
  its own, since `server.py` and `providers/chatgpt/__init__.py` already had
  Phase B's research-mounting code eagerly imported/referenced: trimmed both
  to their pre-research shape, staged, gated (`uv run poe check` green, 59
  tests), committed, then restored the full Phase B content for commit (2).
  Verified each commit's isolated state with a `git stash push --keep-index
  -u` round-trip (stash everything not staged, run the gate, restore) rather
  than trusting the diff alone. Left three pre-existing, unrelated dirty
  files (`docs/discovery/README.md`'s *other* index entry,
  `scripts/dbx_models_probe.py`, `scripts/har_explore.py` — see the
  2026-07-11 "Pre-existing uncommitted changes" finding below) out of both
  commits by extracting just this work's own diff/entries where a file's
  changes were mixed with theirs (`docs/discovery/README.md`: reconstructed
  a "HEAD + only this session's new index entry" version by hand rather than
  staging the whole modified file). Two stash entries
  (`wip: phase B/C ...`, `wip: phase C ...`) are left in the stash list,
  fully superseded/redundant (everything in them was individually verified
  and extracted already) but not dropped -- dropping a stash wasn't
  explicitly authorized, so they're harmless leftovers for the user to clear
  (`git stash clear`) whenever convenient, not something a future session
  needs to consult.

- **2026-07-11 — Phase B shipped the emulated research backend only; the Deep
  Research backend is an honest, documented stub, not a guess.** Reasoning:
  the account this was built against is confirmed free-tier (per
  docs/discovery/2026-07-10-tool-calling.md Update 5), and ChatGPT Deep
  Research has historically been a paid-tier-only feature -- so before
  spending a live-browser discovery session hunting for a trigger field
  (candidates would've been guesses: `system_hints`, `conversation_mode`,
  `enabled_tools`/`disabled_tools`, per the plan's own risk notes), the
  account almost certainly can't exercise it at all. Per this project's own
  rule (never guess APIs -- verify by reading/capturing first) and the "don't
  guess" instruction, `research/backends/deep_research.py` ships as a real
  class satisfying the `ResearchBackend` port with `available()` hardcoded to
  `False` and a `NotImplementedError` + docstring pointing at exactly what a
  future session needs to do (capture the outgoing `f/conversation` body
  while toggling Deep Research in the real UI, on an account that has it).
  `research.backends.resolve_backend` already prefers it over `emulated` the
  moment `available()` returns True -- **no other code needs to change** when
  that discovery session eventually happens. This is the plan's own risk
  mitigation (§11: "emulated ships first ... deep_research is additive behind
  an availability probe") playing out exactly as designed. Full reasoning +
  concrete next steps: `docs/discovery/2026-07-11-deep-research-scoping.md`.
- **2026-07-11 — Emulated research backend live-verified end-to-end, first
  try:** `webllm-proxy research "What is the toolz Python library used for?"`
  → real ChatGPT web search (cited `toolz.readthedocs.io` and the PyPI page,
  not fabricated) → a correctly-structured `# Research Report` /
  `## Summary` / `## Findings` / `## Sources` markdown report, in ~10s. Also
  verified the REST API directly: `GET /v1/research` (list), `GET .../<id>`,
  `DELETE .../<id>` (204, then 404 on re-fetch), 404 for an unknown id, 400
  for an empty `query`. No prompt-injection framing was needed (unlike the
  tool-calling contract) -- the research prompt is just normal task
  instructions, so `gpt-5-mini` (and likely other models) comply without the
  "SYSTEM INSTRUCTIONS outranks you" framing that `auto`/`gpt-5-5` refuse.
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
