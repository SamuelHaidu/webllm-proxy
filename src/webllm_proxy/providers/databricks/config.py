"""Databricks provider config (env-driven).

The workspace URL (with its `?o=<org-id>` query) selects the workspace host and
the org id that the in-page fetch sends as `x-databricks-org-id`. It is required
for `login`/`serve`.
"""
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ...core import env

WORKSPACE_URL = env.env_str("DATABRICKS_PROXY_URL").strip()

PROFILE_DIR = Path(env.env_str("DATABRICKS_PROXY_PROFILE")
                   or (env.data_dir("databricks-proxy") / "profile"))
HEADLESS = env.flag("DATABRICKS_PROXY_HEADLESS", True)
HOST = env.env_str("DATABRICKS_PROXY_HOST", "127.0.0.1")
PORT = env.env_int("DATABRICKS_PROXY_PORT", 5103)
DEBUG_DUMP = env.flag("DATABRICKS_PROXY_DEBUG_DUMP", False)

# llmproxy routing envelope. The client_id gates model entitlements (MEC); the
# editor-agent client is the one with Claude Sonnet 4.5 enabled on the dev acct.
LLMPROXY_PATH = "/ajax-api/2.0/conversation/llmproxy/"
CLIENT_ID = env.env_str("DATABRICKS_PROXY_CLIENT_ID", "editor-assistant-agent-mode")
AGENT_NAME = env.env_str("DATABRICKS_PROXY_AGENT_NAME", "GenieCodeFullChat")
ANTHROPIC_ENDPOINT = "anthropic/v1/messages"
DEFAULT_MODEL = env.env_str("DATABRICKS_PROXY_MODEL", "claude-4-5-sonnet")

# Models advertised on GET /v1/models (the enabled ones on this account).
ENABLED_MODELS = [m.strip() for m in env.env_str(
    "DATABRICKS_PROXY_MODELS", "claude-4-5-sonnet").split(",") if m.strip()]


def org_id() -> str:
    q = parse_qs(urlsplit(WORKSPACE_URL).query)
    return (q.get("o") or [""])[0]
