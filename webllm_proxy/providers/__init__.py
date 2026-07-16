"""Provider registry. Builds the enabled providers (each with its own browser
session, not yet started) from the parsed config, and runs one-time logins.

Imports are lazy so selecting one backend doesn't import the others.
"""

from __future__ import annotations

from ..utils.chrome_import import imported_extension_paths
from ..utils.config import Config
from .base import BrowserBackedProvider

PROVIDERS = ("chatgpt", "databricks", "copilot")


def build_provider(name: str, config: Config) -> BrowserBackedProvider:
    pc = getattr(config.providers, name)
    profile = config.profile_dir(name)
    # Load only extensions already copied into our own data dir; this never reads
    # the installed Chrome profile (that happens in `login` / `import-extensions`).
    ext = imported_extension_paths(pc, name)
    if name == "chatgpt":
        from . import chatgpt

        session = chatgpt.build_session(pc.headless, profile, extension_paths=ext)
        return chatgpt.ChatgptProvider(
            session, system_prompt=pc.system_prompt_for, user_suffix=pc.user_suffix_for
        )
    if name == "databricks":
        from . import databricks

        session = databricks.build_session(
            pc.headless, profile, pc.workspace_url, extension_paths=ext
        )
        return databricks.DatabricksProvider(
            session,
            workspace_url=pc.workspace_url,
            style_rules=pc.style_rules,
            system_prompt=pc.system_prompt_for,
            user_suffix=pc.user_suffix_for,
        )
    if name == "copilot":
        from . import copilot

        nav_url = pc.url or copilot.NAV_URL
        session = copilot.build_session(pc.headless, profile, nav_url, extension_paths=ext)
        return copilot.CopilotProvider(
            session,
            nav_url=nav_url,
            system_prompt=pc.system_prompt_for,
            user_suffix=pc.user_suffix_for,
        )
    raise ValueError(f"unknown provider {name!r} (choose from: {', '.join(PROVIDERS)})")


def build_enabled(config: Config) -> dict[str, BrowserBackedProvider]:
    return {name: build_provider(name, config) for name in config.enabled_providers()}


def login(name: str, config: Config) -> bool:
    from ..gateways.cloakbrowser import run_login
    from ..utils.chrome_import import import_extensions

    pc = getattr(config.providers, name)
    profile = config.profile_dir(name)
    # Opt-in: copy the installed Chrome's extensions into this profile now, while
    # the user is running an explicit command (no-op unless enabled in config).
    imported = import_extensions(pc, name)
    if imported:
        print(f"[{name}] imported {len(imported)} Chrome extension(s) into the proxy profile")
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
