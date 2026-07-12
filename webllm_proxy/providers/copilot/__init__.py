"""copilot — a client for Microsoft Copilot's chat protocols with a familiar,
OpenAI-SDK-style API.

Primary API (mirrors `openai` / `openai.AsyncOpenAI`):

    from webllm_proxy.providers.copilot import Copilot
    client = Copilot(edition="m365", api_key=TOKEN, conversation=CONV_ID)
    resp = client.chat.completions.create(
        model="think", messages=[{"role": "user", "content": "hello"}],
    )
    print(resp.choices[0].message.content)

Supports the M365 BizChat (`substrate.office.com`) and consumer
(`copilot.microsoft.com`) editions behind one surface (SignalR "Sydney" and the
consumer event protocol). The low-level engine (`CopilotClient`, `editions/`,
`protocol/` codecs) stays available for advanced use; inside `webllm_proxy` this
package is also wired as the `copilot` provider (`provider.py`).

See `README.md` and the protocol notes under `docs/protocol/`.
"""

from __future__ import annotations

# ---- engine + normalized types (advanced) ----------------------------------
from .client import CopilotClient
from .editions import Edition, get_edition
from .exceptions import (
    CaptchaRequired,
    ChallengeError,
    ConversationLimitError,
    CopilotError,
    ProtocolError,
    ThrottledError,
    TransportError,
)
from .models import (
    Citation,
    ConversationRef,
    Delta,
    Event,
    Final,
    Model,
    ModelInfo,
    Progress,
    Suggestion,
    Throttling,
)
from .provider import CopilotProvider

# ---- primary: OpenAI-style API ---------------------------------------------
from .sdk import (
    APIConnectionError,
    APIError,
    AsyncCopilot,
    AuthenticationError,
    BadRequestError,
    ChatCompletion,
    ChatCompletionChunk,
    ChatMessage,
    Choice,
    ChoiceDelta,
    ChunkChoice,
    Copilot,
    ModelsPage,
    RateLimitError,
)

__version__ = "0.2.0"

__all__ = [
    # OpenAI-style (primary)
    "Copilot",
    "AsyncCopilot",
    "ChatCompletion",
    "ChatCompletionChunk",
    "ChatMessage",
    "Choice",
    "ChoiceDelta",
    "ChunkChoice",
    "ModelsPage",
    "APIError",
    "APIConnectionError",
    "RateLimitError",
    "AuthenticationError",
    "BadRequestError",
    # engine + types (advanced)
    "CopilotProvider",
    "CopilotClient",
    "Edition",
    "get_edition",
    "Model",
    "ModelInfo",
    "ConversationRef",
    "Delta",
    "Progress",
    "Final",
    "Event",
    "Citation",
    "Suggestion",
    "Throttling",
    "CopilotError",
    "ProtocolError",
    "ThrottledError",
    "TransportError",
    "ConversationLimitError",
    "ChallengeError",
    "CaptchaRequired",
]
