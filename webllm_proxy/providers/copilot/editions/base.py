"""Edition contract: everything that differs between Copilot variants lives here,
so the core (`client`, `protocol`) stays universal.

An `Edition` answers five questions:
  1. which wire protocol? (`make_codec`)
  2. how do I create/attach a conversation? (`create_conversation`)
  3. what WebSocket URL + headers? (`ws_url`, `ws_headers`)
  4. how does the normalized `Model` map to this edition's field? (`map_model`)
  5. what extra per-turn options does the codec need? (`send_options`)
"""

from __future__ import annotations

import abc
from urllib.parse import urlencode

from ..auth import Credential
from ..models import ConversationRef, Model, ModelInfo
from ..protocol.base import ProtocolCodec

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


def build_ws_url(base: str, params: dict[str, str], credential: Credential) -> str:
    p = dict(params)
    credential.apply_query(p)
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{urlencode(p)}"


class Edition(abc.ABC):
    name: str
    #: normalized Model -> edition-specific field value (`tone`/`mode`)
    model_map: dict[Model, str] = {}

    @abc.abstractmethod
    def make_codec(self) -> ProtocolCodec: ...

    @abc.abstractmethod
    def ws_url(self, conversation: ConversationRef, credential: Credential) -> str: ...

    def ws_headers(self) -> dict[str, str]:
        return {"User-Agent": USER_AGENT}

    def map_model(self, model: Model | str) -> str:
        """Resolve a model to this edition's wire value. A raw string (a
        discovered `ModelInfo.id`, e.g. `"Gpt_5_5_Reasoning"`) passes through
        unchanged; a normalized `Model` maps via `model_map`."""
        if isinstance(model, str):
            return model
        if model in self.model_map:
            return self.model_map[model]
        return self.model_map.get(Model.AUTO, model.value)

    # ---- model discovery (replaces hardcoded lists) ----------------------
    def default_models(self) -> list[ModelInfo]:
        """Known-good fallback list when live discovery isn't available."""
        return []

    @staticmethod
    def parse_models(source: dict) -> list[ModelInfo]:
        """Pure parse of the edition's capability/config document into models."""
        return []

    async def discover_models(self, http, credential: Credential) -> list[ModelInfo]:
        """Fetch the capability document and parse it, falling back to
        `default_models()` on any error/unavailability."""
        return self.default_models()

    @abc.abstractmethod
    def send_options(self, model: Model | str, conversation: ConversationRef) -> dict: ...

    async def create_conversation(self, http, credential: Credential) -> ConversationRef:
        """Create/obtain a conversation. Editions that can't do this off-browser
        raise `NotImplementedError` (pass an explicit `ConversationRef` instead)."""
        raise NotImplementedError(
            f"{self.name}: obtain a conversation id from a browser session and "
            "pass it explicitly"
        )
