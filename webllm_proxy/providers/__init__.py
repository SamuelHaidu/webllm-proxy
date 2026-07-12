"""Provider registry. Imports are lazy so selecting one backend doesn't import
the other's config (which reads that backend's environment)."""

from ..domain.ports import Provider

PROVIDERS = ("chatgpt", "databricks", "copilot")


def get_provider(name: str, host: str | None = None, port: int | None = None) -> Provider:
    if name == "chatgpt":
        from .chatgpt import ChatGptProvider

        return ChatGptProvider(host, port)
    if name == "databricks":
        from .databricks import DatabricksProvider

        return DatabricksProvider(host, port)
    if name == "copilot":
        from .copilot.provider import CopilotProvider

        return CopilotProvider(host, port)
    raise ValueError(f"unknown provider {name!r} (choose from: {', '.join(PROVIDERS)})")
