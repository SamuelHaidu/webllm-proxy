"""Consumer edition — `copilot.microsoft.com` (free / Copilot Pro).

Wire protocol B (event JSON). Anti-bot: HTTP Cloudflare Turnstile (needs a real/
stealth browser) + in-band hashcash (handled by the codec).
"""

from __future__ import annotations

import uuid

from ..auth import Credential
from ..models import ConversationRef, Model, ModelInfo
from ..protocol.base import ProtocolCodec
from ..protocol.events import EventCodec
from .base import Edition, build_ws_url

_WS_BASE = "wss://copilot.microsoft.com/c/api/chat"
_START_URL = "https://copilot.microsoft.com/c/api/start"
_ORIGIN = "https://copilot.microsoft.com"


class ConsumerEdition(Edition):
    name = "consumer"
    model_map = {
        Model.AUTO: "smart",
        Model.FAST: "smart",
        Model.THINK: "smart",
        Model.RESEARCH: "deep-research",
    }

    def make_codec(self) -> ProtocolCodec:
        return EventCodec()

    # ---- model discovery -------------------------------------------------
    def default_models(self) -> list[ModelInfo]:
        return [
            ModelInfo("smart", "Smart", "Adaptive default", default=True),
            ModelInfo("deep-research", "Deep Research", "Multi-step research", reasoning=True),
        ]

    @staticmethod
    def parse_models(source: dict) -> list[ModelInfo]:
        """Derive modes from `/c/api/start` feature flags (e.g.
        `smart-mode-default`, `deep-research-nano`, `copilot-beta`)."""
        feats = source.get("features") or (source.get("start") or {}).get("features") or []
        found: dict[str, ModelInfo] = {}
        for f in feats:
            if not isinstance(f, str):
                continue
            if "deep-research" in f:
                found.setdefault(
                    "deep-research", ModelInfo("deep-research", "Deep Research", reasoning=True)
                )
            elif "smart-mode" in f:
                found.setdefault("smart", ModelInfo("smart", "Smart", default=True))
        return list(found.values())

    async def discover_models(self, http, credential: Credential) -> list[ModelInfo]:
        if http is None:
            return self.default_models()
        try:
            conv = await self.create_conversation(http, credential)  # POST /c/api/start
            return self.parse_models(conv.extra.get("start", {})) or self.default_models()
        except Exception:
            return self.default_models()

    def ws_url(self, conversation: ConversationRef, credential: Credential) -> str:
        params = {"api-version": "2", "clientSessionId": str(uuid.uuid4())}
        # credential is expected to be QueryToken(param="accessToken")
        return build_ws_url(_WS_BASE, params, credential)

    def ws_headers(self) -> dict[str, str]:
        return {**super().ws_headers(), "Origin": _ORIGIN}

    def send_options(self, model: Model | str, conversation: ConversationRef) -> dict:
        return {"mode": self.map_model(model), "context": {}}

    async def create_conversation(self, http, credential: Credential) -> ConversationRef:
        if http is None:
            raise RuntimeError("consumer create_conversation needs an httpx.AsyncClient")
        body = {
            "startNewConversation": True,
            "teenSupportEnabled": True,
            "correctPersonalizationSetting": True,
            "performUserMerge": True,
            "deferredDataUseCapable": True,
        }
        headers = {
            "Origin": _ORIGIN,
            "User-Agent": self.ws_headers()["User-Agent"],
            **credential.headers(),
        }
        resp = await http.post(_START_URL, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return ConversationRef(id=data["currentConversationId"], extra={"start": data})
