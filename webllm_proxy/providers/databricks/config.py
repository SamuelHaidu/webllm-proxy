"""Databricks provider config (env-driven).

The workspace URL (with its `?o=<org-id>` query) selects the workspace host and
the org id that the in-page fetch sends as `x-databricks-org-id`. It is required
for `login`/`serve`.
"""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ...infra import env

WORKSPACE_URL = env.env_str("DATABRICKS_PROXY_URL").strip()

PROFILE_DIR = Path(
    env.env_str("DATABRICKS_PROXY_PROFILE") or (env.data_dir("databricks-proxy") / "profile")
)
HEADLESS = env.flag("DATABRICKS_PROXY_HEADLESS", True)
# Prepend token-efficiency / response-style rules (from claude-token-efficient)
# to the system prompt so replies are terse and tool-first. Off = Genie framing
# only.
STYLE_RULES = env.flag("DATABRICKS_PROXY_STYLE_RULES", True)
HOST = env.env_str("DATABRICKS_PROXY_HOST", "127.0.0.1")
PORT = env.env_int("DATABRICKS_PROXY_PORT", 5103)
DEBUG_DUMP = env.flag("DATABRICKS_PROXY_DEBUG_DUMP", False)

# llmproxy routing envelope. The client_id gates model entitlements (MEC); the
# editor-agent client is the one with Claude Sonnet 4.5 enabled on the dev acct.
LLMPROXY_PATH = "/ajax-api/2.0/conversation/llmproxy/"
CLIENT_ID = env.env_str("DATABRICKS_PROXY_CLIENT_ID", "editor-assistant-agent-mode")
AGENT_NAME = env.env_str("DATABRICKS_PROXY_AGENT_NAME", "GenieCodeFullChat")
ANTHROPIC_ENDPOINT = "anthropic/v1/messages"
# Anthropic's token-counting endpoint (`POST /v1/messages/count_tokens` -> the
# same request shape, returns `{"input_tokens": N}` without generating). The
# Genie editor never calls it (not in any HAR), so llmproxy support is unverified.
ANTHROPIC_COUNT_TOKENS_ENDPOINT = "anthropic/v1/messages/count_tokens"
DEFAULT_MODEL = env.env_str("DATABRICKS_PROXY_MODEL", "claude-4-5-sonnet")

# Models advertised on GET /v1/models (the enabled ones on this account).
ENABLED_MODELS = [
    m.strip()
    for m in env.env_str("DATABRICKS_PROXY_MODELS", "claude-4-5-sonnet").split(",")
    if m.strip()
]

# Advertised max output tokens for the Claude (Anthropic) channel, surfaced on
# GET /v1/models as `_max_tokens`. Claude reasoning eats into max_tokens (a
# client's thinking budget must fit under it), so a realistic cap matters: pi
# derives the thinking budget from this, and an 8k default would throttle
# high-effort thinking to ~7k. 64000 is the proven ceiling on this channel
# (see docs/discovery/2026-07-10-databricks-llmproxy.md).
CLAUDE_MAX_TOKENS = env.env_int("DATABRICKS_PROXY_CLAUDE_MAX_TOKENS", 64000)

# Azure OpenAI channel: a second llmproxy sub-path (`proxy/chat/completions`)
# serving this account's enabled GPT-4.1 deployments. OpenAI-shaped request +
# streaming SSE; envelope is `{params, metadata.clientId, @method, deployment,
# apiVersion}` (NOT the `_llmproxy_fields` used by the Anthropic channel). Exposed
# as OpenAI `/v1/chat/completions`. See docs/discovery/2026-07-10-databricks-*.
CHAT_COMPLETIONS_PATH = "/ajax-api/2.0/conversation/proxy/chat/completions"
AZURE_CLIENT_ID = env.env_str("DATABRICKS_PROXY_AZURE_CLIENT_ID", "auto-rename-action")
AZURE_API_VERSION = env.env_str("DATABRICKS_PROXY_AZURE_API_VERSION", "2025-01-01-preview")
OPENAI_MODELS = [
    m.strip()
    for m in env.env_str(
        "DATABRICKS_PROXY_OPENAI_MODELS", "gpt-41-2025-04-14,gpt-41-mini-2025-04-14"
    ).split(",")
    if m.strip()
]


def org_id() -> str:
    q = parse_qs(urlsplit(WORKSPACE_URL).query)
    return (q.get("o") or [""])[0]
