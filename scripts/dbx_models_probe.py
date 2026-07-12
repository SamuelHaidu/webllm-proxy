"""Discover which model registrations are usable on the current Databricks login.

Databricks' `llmproxy` gates models per `clientId` (MEC entitlements), and there
is no list endpoint — so we map the registry by *trial + error-message taxonomy*:
send a tiny request per candidate model on each channel and classify the reply.

Two channels are probed (both under `POST /ajax-api/2.0/conversation/...`):
  - anthropic : `llmproxy/`            body -> `_llmproxy_fields.endpoint =
                                       anthropic/v1/messages` (Claude family)
  - azure     : `proxy/chat/completions`  Azure-OpenAI shape (GPT family)

Classification (from HTTP status + response body):
  WORKS      200 + real content            -> usable on this login/client
  DISABLED   PERMISSION_DENIED / MODEL_DISABLED / MEC  -> registered but the
             clientId isn't entitled (the name is real; another clientId might)
  NOT_FOUND  NOT_FOUND / "not registered"  -> no such registration/alias
  OTHER      anything else (status printed)

Why a direct in-page fetch (not the running proxy): the proxy's CDP passthrough
swallows the *error body*, so through it you can only see WORKS-vs-not. Reading
`response.text()` in-page surfaces the descriptive DISABLED/NOT_FOUND reason,
which tells you which gated models are real (and thus reachable if entitlements
change). The proxy holds a single-instance profile lock, so stop `serve` first.

Usage:
    # stop any running `webllm-proxy serve --provider databricks` first
    DATABRICKS_PROXY_URL="https://<ws>.cloud.databricks.com/?o=<org>" \
        uv run python scripts/dbx_models_probe.py [anthropic|azure|both] [model,model,...]

Prints only status + classification + a short redacted reason; never tokens.
"""

import sys
import time
import uuid

from cloakbrowser import launch_persistent_context

from webllm_proxy.providers.databricks import config

LLMPROXY_PATH = "/ajax-api/2.0/conversation/llmproxy/"
CHAT_PATH = "/ajax-api/2.0/conversation/proxy/chat/completions"

# Default candidate registrations to try, per channel (extend via CLI arg 2).
DEFAULT_ANTHROPIC = [
    "claude-4-5-sonnet",
    "claude-sonnet-4-5",
    "claude-4-5-haiku",
    "claude-4-5-opus",
    "claude-4-sonnet",
    "claude-4-opus",
    "claude-4-1-opus",
    "claude-3-7-sonnet",
    "claude-3-5-sonnet",
    "gemini-2-5-pro",
    "llama-3-3-70b",
    "llama-3-1-405b",
]
DEFAULT_AZURE = [
    "gpt-41-2025-04-14",
    "gpt-41-mini-2025-04-14",
    "gpt-4o-2024-11-20",
    "gpt-4o-mini",
    "gpt-5",
    "o3-mini",
]

# In-page: read a fresh CSRF token, POST the candidate body, return status+body.
_FETCH_JS = r"""
async (arg) => {
  const s = await (await fetch('/auth/session/info', {credentials:'include'})).json();
  const res = await fetch(arg.path, {method:'POST', credentials:'include',
    headers:{'content-type':'application/json', 'accept':'application/json',
             'x-csrf-token': s.csrfToken, 'x-databricks-org-id': String(arg.org)},
    body: JSON.stringify(arg.body)});
  let text=''; try { text = await res.text(); } catch(e){ text = '<'+e+'>'; }
  return {status: res.status, body: text.slice(0, 600)};
}
"""


def anthropic_body(model):
    return {
        "system": [{"type": "text", "text": "hi"}],
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 8,
        "stream": False,
        "_llmproxy_fields": {
            "model_registration": model,
            "endpoint": "anthropic/v1/messages",
            "agent_name": "GenieCodeFullChat",
            "client_id": "editor-assistant-agent-mode",
            "trace_id": str(uuid.uuid4()),
            "call_id": str(uuid.uuid4()),
        },
    }


def azure_body(model):
    return {
        "params": {
            "messages": [{"role": "user", "content": "hi"}],
            "model": model,
            "temperature": 0,
            "stream": False,
        },
        "metadata": {"traceId": str(uuid.uuid4()), "clientId": "auto-rename-action"},
        "@method": "openAiServiceChatCompletionRequest",
        "deployment": model,
        "model": model,
        "apiVersion": "2025-01-01-preview",
    }


def classify(status, body):
    b = (body or "").lower()
    if status == 200 and any(
        k in body for k in ('"completion"', "message_start", '"choices"', '"content"')
    ):
        return "WORKS", ""
    if any(k in b for k in ("model_disabled", "permission_denied", "failed check: mec")):
        return "DISABLED", "registered but clientId not entitled"
    if any(k in b for k in ("not_found", "not registered", "does not match")):
        return "NOT_FOUND", "no such registration/alias"
    return "OTHER", f"status={status} {(body or '')[:80]}".strip()


def probe(page, org, channel, model):
    path = LLMPROXY_PATH if channel == "anthropic" else CHAT_PATH
    body = anthropic_body(model) if channel == "anthropic" else azure_body(model)
    try:
        r = page.evaluate(_FETCH_JS, {"path": path, "org": org, "body": body})
    except Exception as e:
        return "OTHER", f"eval-error: {e}"
    return classify(r.get("status"), r.get("body"))


def main():
    channel = sys.argv[1] if len(sys.argv) > 1 else "both"
    channels = ["anthropic", "azure"] if channel == "both" else [channel]
    override = sys.argv[2].split(",") if len(sys.argv) > 2 else None

    if not config.WORKSPACE_URL:
        sys.exit("DATABRICKS_PROXY_URL is not set (workspace URL with ?o=<org>).")
    org = config.org_id()
    print(f"profile={config.PROFILE_DIR}")
    print(f"workspace={config.WORKSPACE_URL.split('?')[0]}  org={org[:4]}…\n")

    ctx = launch_persistent_context(str(config.PROFILE_DIR), headless=True)
    try:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(config.WORKSPACE_URL, wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        uid = page.evaluate(
            "async()=>{try{const r=await fetch('/auth/session/info',"
            "{credentials:'include'});return (await r.json()).userId||null;}"
            "catch(e){return null;}}"
        )
        if not uid:
            sys.exit("NOT logged in — run `webllm-proxy login --provider databricks` first.")
        print("authenticated: yes\n")
        for ch in channels:
            models = override or (DEFAULT_ANTHROPIC if ch == "anthropic" else DEFAULT_AZURE)
            print(f"== channel: {ch} ==")
            usable = []
            for m in models:
                verdict, reason = probe(page, org, ch, m)
                mark = "✅" if verdict == "WORKS" else "  "
                print(f"  {mark} {m:26s} {verdict:9s} {reason}")
                if verdict == "WORKS":
                    usable.append(m)
            print(f"  -> usable on {ch}: {usable or 'none'}\n")
    finally:
        ctx.close()


if __name__ == "__main__":
    main()
