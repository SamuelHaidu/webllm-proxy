"""ChatGPT provider config (env-driven). Profile dir + env names are kept
back-compatible with the old `chatgpt-proxy`, so existing logins still work."""
from pathlib import Path

from ...core import env

CHATGPT_URL = "https://chatgpt.com"

PROFILE_DIR = Path(env.env_str("CHATGPT_PROXY_PROFILE")
                   or (env.data_dir("chatgpt-proxy") / "profile"))
HEADLESS = env.flag("CHATGPT_PROXY_HEADLESS", True)
HOST = env.env_str("CHATGPT_PROXY_HOST", "127.0.0.1")
PORT = env.env_int("CHATGPT_PROXY_PORT", 5102)
DEBUG_DUMP = env.flag("CHATGPT_PROXY_DEBUG_DUMP", False)
