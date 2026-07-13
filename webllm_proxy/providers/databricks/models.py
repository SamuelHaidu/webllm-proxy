"""Parse the Databricks `graphql/ConversationModelStatuses` response.

The workspace SPA sends one query listing model availability *per clientId*
(MEC entitlements differ by client). We drive requests as the
`editor-assistant-agent-mode` client (see `llmproxy.CLIENT_ID`), so we keep only
the models that are AVAILABLE **for that clientId** — that is the exact set the
llmproxy channel will actually accept. Response shape (pinned from a live HAR,
see docs/discovery/2026-07-13-databricks-model-discovery.md):

    data.conversationListModelAvailability.modelAvailability[] = {
        clientId, modelStatuses[] = { isAvailable, name, status }, ...
    }

The parser is defensive: any unexpected shape yields `[]` so the caller can
degrade gracefully instead of raising.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path

from .llmproxy import CLIENT_ID

_DISCOVERY_ASSET = Path(__file__).resolve().parent / "model_discovery.json"


@cache
def discovery_request() -> dict:
    """The pinned `ConversationModelStatuses` request (operationName / operationId
    / clientIds / query). The server safelists this exact operation via the
    operationId signature, so the provider and probe replay it verbatim. See
    model_discovery.json for how to re-capture it if Databricks changes it."""
    return json.loads(_DISCOVERY_ASSET.read_text(encoding="utf-8"))


def _available_name(status) -> str | None:
    """The model name if this status entry is AVAILABLE, else None."""
    if not isinstance(status, dict):
        return None
    name = status.get("name")
    if not isinstance(name, str) or not name:
        return None
    if status.get("status") == "AVAILABLE" or status.get("isAvailable") is True:
        return name
    return None


def _names_for_client(entry: dict, client_id: str) -> list[str]:
    if entry.get("clientId") != client_id:
        return []
    statuses = entry.get("modelStatuses")
    if not isinstance(statuses, list):
        return []
    return [name for status in statuses if (name := _available_name(status))]


def parse_model_statuses(response, client_id: str = CLIENT_ID) -> list[str]:
    """Return the AVAILABLE model names for `client_id`, in response order."""
    try:
        availability = response["data"]["conversationListModelAvailability"]["modelAvailability"]
    except (KeyError, TypeError):
        return []
    if not isinstance(availability, list):
        return []
    out: list[str] = []
    for entry in availability:
        if isinstance(entry, dict):
            for name in _names_for_client(entry, client_id):
                if name not in out:
                    out.append(name)
    return out
