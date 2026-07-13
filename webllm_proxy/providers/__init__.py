"""Provider registry. Builds the enabled providers (each with its own browser
session, not yet started) from the parsed config, and runs one-time logins.

Imports are lazy so selecting one backend doesn't import the others.
"""

from __future__ import annotations

from ..utils.config import Config
from .base import BrowserBackedProvider

PROVIDERS = ("chatgpt", "databricks", "copilot")


def build_provider(name: str, config: Config) -> BrowserBackedProvider:
    pc = getattr(config.providers, name)
    profile = config.profile_dir(name)
    if name == "chatgpt":
        from . import chatgpt

        session = chatgpt.build_session(pc.headless, profile)
        return chatgpt.ChatgptProvider(session)
    if name == "databricks":
        from . import databricks

        session = databricks.build_session(pc.headless, profile, pc.workspace_url)
        return databricks.DatabricksProvider(
            session,
            workspace_url=pc.workspace_url,
            style_rules=pc.style_rules,
        )
    if name == "copilot":
        from . import copilot

        nav_url = pc.url or copilot.NAV_URL
        session = copilot.build_session(pc.headless, profile, nav_url)
        return copilot.CopilotProvider(session, nav_url=nav_url)
    raise ValueError(f"unknown provider {name!r} (choose from: {', '.join(PROVIDERS)})")


def build_enabled(config: Config) -> dict[str, BrowserBackedProvider]:
    return {name: build_provider(name, config) for name in config.enabled_providers()}


def login(name: str, config: Config) -> bool:
    from ..gateways.cloakbrowser import run_login

    pc = getattr(config.providers, name)
    profile = config.profile_dir(name)
    if name == "chatgpt":
        from . import chatgpt

        return run_login(
            name=name, nav_url=chatgpt.CHATGPT_URL + "/", profile_dir=profile, authed=chatgpt.authed
        )
    if name == "databricks":
        from . import databricks

        if not pc.workspace_url:
            raise RuntimeError("databricks workspace_url is not set (workspace URL with ?o=).")
        return run_login(
            name=name, nav_url=pc.workspace_url, profile_dir=profile, authed=databricks.authed
        )
    if name == "copilot":
        from . import copilot

        return run_login(
            name=name,
            nav_url=pc.url or copilot.NAV_URL,
            profile_dir=profile,
            authed=copilot.authed,
            steer=copilot.login_steer,
        )
    raise ValueError(f"unknown provider {name!r}")
