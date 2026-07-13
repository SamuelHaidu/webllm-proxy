"""HTTP surface: one Flask app + controllers, decoupled from providers.

`build_app(providers)` mounts `GET /v1/models`, `POST /v1/chat/completions`,
and `GET /health` over the given `{name: Provider}` map.
"""

from .app import build_app

__all__ = ["build_app"]
