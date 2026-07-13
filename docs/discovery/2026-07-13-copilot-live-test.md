# Copilot deep-thinking live smoke + expired-login gotcha

Date: 2026-07-13
Tags: [copilot]

Added a **tiny, opt-in live smoke** for the Copilot deep-thinking model and, in
running it, found the `copilot-proxy` browser profile's login is **expired** plus
a provider bug that masked it.

## The test (`tests/smoke_copilot_reasoning.py`)

Copilot throttles at the request layer after ~20 rapid programmatic turns (see
`2026-07-11-ms365-copilot-sydney.md`, Update 3), so this is **not** in the
`smoke_openai_sdk.py` battery. It boots only the copilot provider against the
logged-in profile and sends **exactly two turns** to the deep-thinking model
(`copilot__Reasoning`, "Think Deeper"): one non-streaming, one streaming, sharing
one headless session. Deterministic prompt (`17 * 23` → ends on `391`).

- Gate: `WEBLLM_PROXY_COPILOT_LIVE=1` (never runs in `poe check`; it's `smoke_*`,
  not `test_*`, and is double-skipped without the gate / without a login).
- Run: `WEBLLM_PROXY_COPILOT_LIVE=1 uv run pytest tests/smoke_copilot_reasoning.py -v`
  (add `WEBLLM_PROXY_COPILOT_HEADLESS=0` to watch it).

## Finding: the profile login is expired

The profile has cookies, but the M365 session no longer completes silent SSO
(DOM-inspection only, no chat turns → no throttle):

- `m365.cloud.microsoft/chat` and `/chat/` → redirect to
  `login.microsoftonline.com/common/oauth2/v2.0/authorize` (title "Sign in to
  your account"); silent SSO does not complete within several seconds.
- `m365.cloud.microsoft/` (root) → the **signed-out marketing splash**, served
  from the app host, title **"Microsoft 365 Copilot - Sign in"**, "Download the
  app" CTA. The chat composer in that hero image is decorative, not interactive.

Fix is a one-time interactive re-login (needs a display):
`webllm-proxy login --provider copilot`. Only then can the positive path (a real
deep-think answer) be validated.

## Bug fixed: `authed()` was hostname-only (false-positive)

`providers/copilot/__init__.py::authed()` only checked the hostname, so it treated
the signed-out splash (same `m365.cloud.microsoft` host) as authenticated. The
session therefore reported `ready`, then failed mid-turn with a confusing
**"copilot composer not found"** instead of surfacing "not logged in".

Hardened it: reject `login.*` hosts, require an app host, and reject pages whose
title contains "sign in" (the splash + login pages). **Fail-open on the title** (a
valid app title never contains "sign in") so a genuine login is never rejected —
important because the positive path can't be re-verified here until re-login. Unit
test: `tests/test_copilot_auth.py` (pure, fake page; 8 cases).

## Status (superseded by the Update below)

- **Negative path verified live**: with the hardened `authed()` the expired
  session reports not-ready, so the smoke **skips cleanly** ("copilot session not
  ready") instead of failing — zero chat turns sent, 0 orphan Chrome, `poe check`
  green (45 tests).
- **Positive path pending**: re-login, then rerun the gated smoke to confirm the
  `copilot__Reasoning` turn returns a real answer.
- Not addressed (out of scope this pass): the provider never clicks the model
  selector, so `copilot__Reasoning` currently drives the page's default tone
  rather than actually engaging "Think Deeper". The smoke validates the id
  round-trips end to end; it does not assert the UI switched tone.

---

## Update: the title-based fix was still wrong; real fix + live confirmation

The user then ran `webllm-proxy login --provider copilot`, logged in in the
browser window — and the polling loop **still never detected it**. My first fix
(reject titles containing "sign in") was itself the bug: `m365.cloud.microsoft/`
is a **marketing homepage** whose `<title>` stays **"Microsoft 365 Copilot - Sign
in"** even when the session is fully authenticated (confirmed live: DOM probe on
the root while logged in still shows that exact title). So the poll's `authed()`
could never return `True` while navigating the site root — title is not a valid
signed-in/out signal at all on this host.

**Better signal, found by inspecting the actually-logged-in page (no chat turn,
no throttle):**

| state | `/chat` | root `/` |
|---|---|---|
| **signed in** | stays on `m365.cloud.microsoft/chat`, title `Chat | M365 Copilot`, composer present, aria-labels `New chat`/`Message Copilot`/the user's name | 302s to `/chat` |
| **signed out** | 302s to `login.microsoftonline.com/.../authorize` | signed-out splash, title `... - Sign in`, no composer |

So: a logged-out session **cannot reach `/chat`** at all (it bounces to a
`login.*` host), while a logged-in session **always lands there**. That
path/composer signal is edition-agnostic and locale-agnostic, unlike title text.

Also found: a completed login sometimes settles the browser on
`www.office.com/?trysignin=0` (an off-app landing with no composer) rather than
bouncing back into the app — the old poll would misread that as still-logged-out
forever too.

**Fix applied:**
- `NAV_URL` changed to `https://m365.cloud.microsoft/chat` (drive the chat surface
  directly, not the marketing root).
- `authed()` rewritten: reject `login.*` hosts; require an app host
  (`m365.cloud.microsoft`/`copilot.microsoft.com`); if the URL is on `/chat`,
  that's sufficient; otherwise fall back to a live composer-presence DOM check
  (`[role="textbox"]`/`[contenteditable="true"]`/`textarea` visible). No more
  title inspection anywhere.
- New `login_steer(page)`: a login-poll helper that, when the browser has settled
  off both a `login.*` host and the `/chat` app path (e.g. the office.com
  landing), navigates it back to `NAV_URL` so the next poll tick can confirm via
  `authed()`. Never fires while still on a `login.*` page, so it can't interfere
  with an in-progress sign-in.
- `gateways/cloakbrowser/session.py::run_login()` gained an optional `steer`
  callback, invoked once per poll tick after a failed `authed()` check;
  `providers/__init__.py` wires `copilot.login_steer` in for the copilot login
  flow (chatgpt/databricks pass no steer — unaffected).
- `tests/test_copilot_auth.py` rewritten for the new signal: path-based
  short-circuit, composer-presence fallback, login-host rejection even with a
  stray textbox, plus three `login_steer` cases (reroutes office.com, leaves a
  login page alone, leaves the chat app alone).

**Live re-verification (DOM-only where possible, then the real gated smoke):**
- `authed()` against the now-logged-in profile at the real `NAV_URL`: landed on
  `https://m365.cloud.microsoft/chat`, title `Chat | M365 Copilot`,
  **`authed() == True`** — confirms the poll will now actually detect a
  completed login.
- **Positive path confirmed**: `WEBLLM_PROXY_COPILOT_LIVE=1 pytest
  tests/smoke_copilot_reasoning.py` → **2 passed** (non-streaming + streaming
  turns to `copilot__Reasoning` both returned `391` for `17 * 23`, correctly
  parsed from the SignalR delta stream). Exactly two chat turns sent, as
  designed. 0 orphan Chrome after.
- `uv run poe check` green (51 tests: +6 over the previous 45 — the auth
  rewrite's expanded cases plus the 3 new `login_steer` tests).

## Status (current, before the next update)

Both the login-detection bug and the deep-thinking model round trip are now
**fully resolved and live-verified**. Remaining known gap, unchanged from before:
the provider doesn't click the model selector, so `copilot__Reasoning` still
drives whatever tone the page defaults to rather than an explicit UI switch to
"Think Deeper" — the smoke proves the id round-trips and a real answer comes
back, not that the UI's tone selector was engaged.

---

## Update 2: live model discovery (no static list) + chat/tool-calling smoke

Replaced the hardcoded `_TONES` list with **live discovery** from Copilot's own
capability manifest, and added a smoke covering plain chat + emulated tool
calling — surfacing a real, unfixable-by-us model-alignment limitation on the
tool-calling side.

### Model discovery: `POST /chat {"action":"RefreshNavPane"}`

Confirmed live (no CSRF token or special headers needed, unlike Databricks) that
this shell data endpoint returns
`store.bizchatAsAgentGpt.clientPreferences.modelSelectorMetadata`:

    {
      "defaultModelSelectionId": "Magic",
      "availableModelSelectionOptions": [
        {"id": "Magic", "type": "item", "menuItemTitle": "Auto", ...},
        {"id": "Chat", "type": "item", "menuItemTitle": "Quick Response", ...},
        {"id": "Reasoning", "type": "item", "menuItemTitle": "Think Deeper", ...},
        {"itemGroup": [{"id": "Gpt_5_5_Chat", ...}, {"id": "Gpt_5_5_Reasoning", ...}],
         "id": "OpenAI", "type": "itemGroup", "menuItemTitle": "GPT", ...}
      ]
    }

Exactly the 5 ids the old static `_TONES` list hardcoded — this live call
reproduces them from the account's real, current manifest rather than a
guess frozen in source. No entitlement gate observed here (unlike Databricks'
per-clientId MEC gating): whatever the manifest lists is selectable.

**Shipped**: `providers/copilot/models.py` — `MANIFEST_JS` (in-page fetch,
returns just the one `modelSelectorMetadata` object, not the whole ~8 KB
manifest) + `parse_manifest()` (pure Python: flattens `item`s and one level of
`itemGroup` nesting, in manifest order, verbatim — no filtering, no family
classification, matching the "no mapping" principle from the Databricks fix).
`CopilotProvider.models()` now calls this live on every request; empty list +
logged warning on discovery failure. Removed `_TONES` and the `_reasoning`
boolean flag entirely (it was a hand invented classification, not manifest
data). `tests/test_copilot_models.py` covers the parser against the real
captured shape (5 unit tests, pure, no browser).

**Live-verified**: `provider.models()` via the real `build_provider` path
returned all 5 real ids (`copilot__Magic/Chat/Reasoning/Gpt_5_5_Chat/
Gpt_5_5_Reasoning`) with their real titles, 0 orphan Chrome after.

### Chat + emulated tool calling: `tests/smoke_copilot_chat_tools.py`

New gated smoke (same `WEBLLM_PROXY_COPILOT_LIVE=1` gate, same throttle
discipline): live model-discovery assertion (no turn), one plain chat turn on
`Chat`, one tool-calling turn.

- **Plain chat: works.** `copilot__Chat` answered a simple prompt correctly, live.
- **Emulated tool calling: does NOT work, and it's not a wording problem.**
  Tried 4 live variants total:
  1. `get_weather` tool, the default (chatgpt-tuned) contract prompt
     (`exclusive=True`, "these are the only actions available... no other way to
     run commands") → refused: *"I can't use the tool format or tool named
     `get_weather`... because those are not actually available in this
     conversation."*
  2. Same tool, a new milder contract (`webui_tool_contract_copilot.md`,
     `exclusive=False`, drops the absolute "only these tools exist" claim) →
     no refusal this time, but it **silently bypassed the contract** and
     answered the weather directly using its own real Bing-search grounding
     (citation markers `citeturn1search1` in the reply).
  3. Same tool, contract strengthened further ("use THAT tool instead of your
     own search/browsing... even if you could answer directly some other way")
     → same bypass, still answered directly via its own search.
  4. A **fictitious** tool with no real Copilot equivalent
     (`lookup_internal_ticket`, ticket-status lookup) → explicit refusal again:
     *"I checked for an available ticket-lookup capability and didn't find
     one."* — ruling out "it just prefers its real tools when available" as the
     full explanation; it doesn't trust an externally-declared tool schema
     **at all**, real alternative or not.

  Conclusion: M365 Copilot's own alignment does not accept tool definitions
  declared in user-turn text as legitimate, regardless of framing — it either
  states the tool isn't really available, or (when it happens to have a real
  capability that covers the request) just does the task itself and ignores the
  tag protocol. This is a fundamentally different constraint than chatgpt's web
  model, which the tag contract was originally built and tested against. No
  amount of preamble wording tried here changed the outcome, so further live
  iteration on this specific path isn't a good use of the limited request
  budget (~20 turns before the documented request-layer throttle).

**What shipped anyway**: the milder contract path
(`contract_prompt`/`exclusive` params added to `tags.build_preamble`, defaulting
to the exact previous behavior — chatgpt is unaffected) is real infrastructure,
not a discarded experiment: it's a no-regression, best-effort improvement (no
outright refusal in variant 2 vs. variant 1) and the natural hook if Microsoft's
tuning ever loosens or a better wording is found later. Covered by
`tests/test_tags.py` (exclusive vs. non-exclusive wording, contract-prompt
selection). `test_emulated_tool_call` is marked `xfail(strict=False)` with the
full reasoning above (not skipped) — visible in a live run as `XFAIL`, and would
flip to a loud `XPASS` if this ever starts working, rather than silently
staying green or red forever.

**Final tally this session**: 7 real chat turns sent against the live login
across both smoke files + ad hoc probes (well under the ~20 throttle
threshold); 0 orphan Chrome after every run; `uv run poe check` green (59
tests: +8 over the previous 51 — the model-discovery parser tests, the
`tags.build_preamble` exclusivity tests, and the new chat/tools smoke's
structure).

## Status (current)

- **Login detection**: fixed, live-verified.
- **Deep-thinking model round trip**: works, live-verified.
- **Live model discovery, no static list**: works, live-verified (5/5 real ids).
- **Plain chat**: works, live-verified.
- **Emulated tool calling**: confirmed **not working** on M365 Copilot due to the
  model's own alignment against externally-declared tools — documented, tested
  as an honest `xfail`, not silently swept under a passing assertion.
- Unchanged known gap: the provider still doesn't click the model selector, so
  a requested tone drives the turn in name only until that's wired in.
