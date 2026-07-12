"""The gateway's upstream registry: which per-provider proxy each model
namespace forwards to. Pure config + env -- imports no provider module, so
importing the gateway never boots a provider's environment. Defaults honor the
same `<PROVIDER>_PROXY_HOST/PORT` env vars each provider config reads, so the
gateway follows whatever ports you configured."""

from __future__ import annotations

from dataclasses import dataclass

from ..infra import env

# Default port per provider -- matches each provider config's *_PROXY_PORT default.
_DEFAULT_PORTS = {"chatgpt": 5102, "databricks": 5103, "copilot": 5104}


@dataclass(frozen=True)
class Upstream:
    name: str
    base_url: str  # "http://127.0.0.1:5102" (no trailing slash)


def default_upstreams() -> dict[str, Upstream]:
    out: dict[str, Upstream] = {}
    for name, port in _DEFAULT_PORTS.items():
        prefix = name.upper()  # CHATGPT / DATABRICKS / COPILOT
        host = env.env_str(f"{prefix}_PROXY_HOST", "127.0.0.1")
        p = env.env_int(f"{prefix}_PROXY_PORT", port)
        out[name] = Upstream(name, f"http://{host}:{p}")
    return out


def parse_upstreams(spec: str) -> dict[str, Upstream]:
    """Parse a `name=url,name=url` override into a registry (trailing slashes on
    each url are stripped)."""
    out: dict[str, Upstream] = {}
    for raw in spec.split(","):
        item = raw.strip()
        if not item or "=" not in item:
            continue
        name, url = item.split("=", 1)
        name, url = name.strip(), url.strip().rstrip("/")
        if name and url:
            out[name] = Upstream(name, url)
    return out
