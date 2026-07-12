"""M365 BizChat edition — `substrate.office.com` (consumer MSA & enterprise).

Wire protocol A (SignalR). Auth: AAD Bearer in the `access_token` query
(scope `sydney/v2/.default`, ~8 h). No anti-bot. Tools are server-side plugins
(not client-declarable). The `conversationId` must come from the browser shell
(`POST m365.cloud.microsoft/chat` action set) — `create_conversation` is not
available off-browser.
"""

from __future__ import annotations

from ..auth import Credential
from ..models import ConversationRef, Model, ModelInfo
from ..protocol.base import ProtocolCodec
from ..protocol.signalr import SignalRCodec
from .base import Edition, build_ws_url

_WS_BASE = "wss://substrate.office.com/m365Copilot/Chathub"
# The BizChat shell endpoint that serves the capability manifest (incl. the
# model selector). Needs the m365.cloud.microsoft *shell* session cookies.
_SHELL_URL = "https://m365.cloud.microsoft/chat"

# The server VALIDATES optionsSets: a sparse/empty list is rejected with
# `result.value = "InvalidRequest"` for ANY tone (confirmed live). Ship the full
# realistic set captured from a live BizChat turn. These are non-secret feature
# flags; refresh from a newer capture if the service starts rejecting them.
_OPTIONS_SETS = [
    "search_result_progress_messages_with_search_queries",
    "update_textdoc_response_after_streaming",
    "deepleo_networking_timeout_10minutes_canmore",
    "cwc_flux_image",
    "cwc_code_interpreter",
    "cwc_code_interpreter_amsfix",
    "enable_msa_user",
    "cwcgptv",
    "flux_v3_gptv_enable_upload_multi_image_in_turn_wo_ch",
    "gptvnorm2048",
    "pdnascan",
    "cwc_code_interpreter_citation_fix",
    "code_interpreter_interactive_charts",
    "cwc_code_interpreter_interactive_charts_inline_image",
    "code_interpreter_matplotlib_patching",
    "cwc_fileupload_odb",
    "update_memory_plugin",
    "add_custom_instructions",
    "cwc_flux_v3",
    "flux_v3_progress_messages",
    "enable_batch_token_processing",
    "enable_gg_gpt",
    "flux_v3_image_gen_enable_non_watermarked_storage",
    "flux_v3_image_gen_enable_story",
    "rich_responses",
    "pages_citations",
    "pages_citations_multiturn",
]


class M365Edition(Edition):
    name = "m365"
    model_map = {
        Model.AUTO: "Magic",
        Model.FAST: "Chat",
        Model.THINK: "Reasoning",
        Model.RESEARCH: "Reasoning",
    }

    def make_codec(self) -> ProtocolCodec:
        return SignalRCodec()

    # ---- model discovery -------------------------------------------------
    def default_models(self) -> list[ModelInfo]:
        return [
            ModelInfo("Magic", "Auto", "Decides how long to think", default=True),
            ModelInfo("Chat", "Quick Response", "Answers right away"),
            ModelInfo(
                "Reasoning", "Think Deeper", "Think longer for better answers", reasoning=True
            ),
            ModelInfo("Gpt_5_5_Chat", "GPT 5.5 Quick Response", family="GPT"),
            ModelInfo("Gpt_5_5_Reasoning", "GPT 5.5 Think Deeper", reasoning=True, family="GPT"),
        ]

    @staticmethod
    def parse_models(source: dict) -> list[ModelInfo]:
        """Parse `store.bizchatAsAgentGpt.clientPreferences.modelSelectorMetadata
        .availableModelSelectionOptions` (the model selector in the capability
        manifest). Flattens `itemGroup`s (e.g. the GPT family)."""
        store = source.get("store", source)
        agent = store.get("bizchatAsAgentGpt") or {}
        meta = (agent.get("clientPreferences") or {}).get("modelSelectorMetadata") or {}
        default_id = meta.get("defaultModelSelectionId")
        out: list[ModelInfo] = []

        def add(opt: dict, family: str | None = None) -> None:
            if opt.get("type") == "itemGroup":
                for sub in opt.get("itemGroup") or []:
                    add(sub, family=opt.get("menuItemTitle"))
                return
            oid = opt.get("id")
            if not oid:
                return
            title = opt.get("menuItemTitle") or opt.get("shortTitle")
            out.append(
                ModelInfo(
                    id=oid,
                    title=title,
                    description=opt.get("menuItemDescription"),
                    reasoning=("reason" in oid.lower() or "think" in (title or "").lower()),
                    family=family,
                    default=(oid == default_id),
                )
            )

        for opt in meta.get("availableModelSelectionOptions") or []:
            add(opt)
        return out

    async def discover_models(self, http, credential: Credential) -> list[ModelInfo]:
        if http is None:
            return self.default_models()
        try:
            resp = await http.post(
                _SHELL_URL,
                json={"action": "RefreshNavPane"},
                headers={"x-route-id": "chat", **credential.headers()},
            )
            resp.raise_for_status()
            return self.parse_models(resp.json()) or self.default_models()
        except Exception:
            return self.default_models()

    def ws_url(self, conversation: ConversationRef, credential: Credential) -> str:
        base = f"{_WS_BASE}/{conversation.id}"
        # Minimal query; a real client also sends chatsessionid/X-SessionId/variants.
        # Extra query pairs can be supplied via conversation.extra["query"].
        params = {
            "source": '"officeweb"',
            "product": "Office",
            "agent": "web",
            "scenario": "OfficeWebPaidConsumerCopilot",
            **(conversation.extra.get("query") or {}),
        }
        return build_ws_url(base, params, credential)

    def send_options(self, model: Model | str, conversation: ConversationRef) -> dict:
        opts = {
            "source": "officeweb",
            "scenario": "OfficeWebPaidConsumerCopilot",
            "tone": self.map_model(model),
            "plugins": [{"Id": "BingWebSearch", "Source": "BuiltIn"}],
            "optionsSets": _OPTIONS_SETS,
            "locale": "en-us",
        }
        if conversation.signature:
            opts["conversation_signature"] = conversation.signature
        return opts
