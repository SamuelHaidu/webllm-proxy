# 2026-07-10 — Browser automation setup + Cloudflare / cookie findings

Session goal: stand up an authenticated ChatGPT web session under
`chrome-devtools-mcp` so we can capture the underlying backend network API
(step toward the OpenAI-compatible proxy). This entry records what was
**verified this session**. Network-capture of `backend-api/conversation` is
still pending (blocked on getting a logged-in session — see open decision at
end).

## Verified: launch recipe that works

Flatpak Chrome + external remote-debugging port + chrome-devtools-mcp
attach (see repo root `CLAUDE.md` for the machine facts):

```bash
DISPLAY=:1 flatpak run com.google.Chrome --remote-debugging-port=9333 \
  --no-sandbox --user-data-dir=<NON-DEFAULT PROFILE DIR> \
  --disable-blink-features=AutomationControlled about:blank \
  > /tmp/chrome_flatpak.log 2>&1 &
disown
curl -s http://127.0.0.1:9333/json/version      # confirm endpoint up
chrome-devtools start --browserUrl http://127.0.0.1:9333
chrome-devtools new_page "https://chatgpt.com"
```

## Verified: HEADLESS is blocked by Cloudflare Turnstile

- With `--headless`, navigating to `https://chatgpt.com` lands on **"Just a
  moment..."** with an embedded Cloudflare Turnstile challenge iframe
  ("Verify you are human", host `challenges.cloudflare.com/.../turnstile/`).
  The real page never loads.
- Removing `--headless` (running **headed** against the real X display
  `DISPLAY=:1`) → Cloudflare passes automatically, page loads to the normal
  ChatGPT landing screen (title "ChatGPT", heading "Where should we begin?").
- **Conclusion: run headed, not headless, for chatgpt.com.** Headless is a
  strong bot-detection signal here. (`--disable-blink-features=AutomationControlled`
  is also set; headed + that combo cleared Turnstile without interaction.)

## Verified: a fresh profile has no login (obvious, but the trap)

- A brand-new `--user-data-dir` (we used `/tmp/chrome-profile-discovery`)
  shows the logged-out landing page: header has **"Log in" / "Sign up for
  free"** buttons. Cookies are per-profile; a fresh dir has none.
- **Trap from the previous session:** using a `/tmp/...` profile makes the
  login look like it must be redone every time, because `/tmp` is wiped and
  was treated as throwaway. A **persistent** non-`/tmp` profile dir keeps the
  session on disk and is reused across runs (login persists for as long as
  the ChatGPT session token stays valid — typically weeks).

## Verified: Chrome won't debug the real/default profile (Chrome 136+ rule)

- Chrome refuses `--remote-debugging-port` when `--user-data-dir` is what it
  considers its **default** data dir, printing: *"DevTools remote debugging
  requires a non-default data directory. Specify this using --user-data-dir."*
- For the Flatpak, the real interactive profile
  (`~/.var/app/com.google.Chrome/config/google-chrome`) **is** that default
  dir → it cannot be debugged in place.
- **Corollary:** you also can't just have the user launch their *real*
  running Chrome with a debug port and attach to it — same rule blocks it.
- Google's own recommended workaround (per their Chrome 136 remote-debugging
  note) is literally: **copy your profile to a non-default dir**, or use a
  dedicated non-default profile dir.

## Implication for "it should already have my cookies"

To get a persistent, logged-in automation session there are exactly two clean
paths, both ending in a **persistent, non-default profile dir** that is reused
every session (no repeated logins):

- **(A) Log in once** into a fresh persistent profile dir. No credential
  copying. Costs one login, ever.
- **(B) Seed cookies once** from the real profile into a private persistent
  profile dir (Google's copy-the-profile approach), done to a protected
  location in the user's home (mode 700, **never `/tmp`** — `/tmp` copy of
  credential stores was blocked by the safety classifier in a prior session,
  correctly). Costs zero logins. Copies session cookies (not necessarily
  saved passwords) to a second on-disk location.

Open decision recorded for the user to choose (A) vs (B). Once chosen and a
logged-in session exists, next step is the authenticated network capture.

## Pending / next

- [ ] Get logged-in session (A or B above).
- [ ] Send one real message; capture `backend-api/conversation` request +
      response with `chrome-devtools list_network_requests` /
      `get_network_request`: URL, method, auth header (Bearer token vs
      cookie), request JSON shape, SSE streaming event format, any
      `Openai-Sentinel-*` / proof-of-work / device-check headers.
- [ ] Document how the access-token is obtained (likely
      `GET /api/auth/session` returns a JWT the frontend uses as
      `Authorization: Bearer`).
