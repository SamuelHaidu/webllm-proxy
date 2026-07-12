"""Copilot provider config (env-driven). Edition selects the site + protocol:
`m365` (BizChat, substrate ChatHub, default) or `consumer` (copilot.microsoft.com).
"""

from pathlib import Path

from ...infra import env

EDITION = (env.env_str("COPILOT_PROXY_EDITION", "m365").strip().lower()) or "m365"

_NAV = {
    "m365": "https://m365.cloud.microsoft/",
    "consumer": "https://copilot.microsoft.com/",
}
NAV_URL = env.env_str("COPILOT_PROXY_URL") or _NAV.get(EDITION, _NAV["m365"])

PROFILE_DIR = Path(
    env.env_str("COPILOT_PROXY_PROFILE") or (env.data_dir("copilot-proxy") / "profile")
)
HEADLESS = env.flag("COPILOT_PROXY_HEADLESS", True)
HOST = env.env_str("COPILOT_PROXY_HOST", "127.0.0.1")
PORT = env.env_int("COPILOT_PROXY_PORT", 5104)
DEBUG_DUMP = env.flag("COPILOT_PROXY_DEBUG_DUMP", False)

# ChatHub WebSocket URL substrings whose server->client frames carry the answer
# (captured over CDP in transport/browser.py).
CHATHUB_MATCH = ("/m365Copilot/Chathub/", "/c/api/chat")
