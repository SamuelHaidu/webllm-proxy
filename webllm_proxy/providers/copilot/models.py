"""Edition-agnostic, normalized data model.

Every edition/protocol is decoded into these types, so callers never see
SignalR frames, `appendText` events, `tone` vs `mode`, etc. This is the public
surface a UI or an API bridge consumes.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Model(enum.Enum):
    """Normalized model/effort selector, mapped per edition (`tone`/`mode`)."""

    AUTO = "auto"       # let the service decide (M365 `Magic`, consumer `smart`)
    FAST = "fast"       # quick, no reasoning (M365 `Chat`)
    THINK = "think"     # reasoning / "think deeper" (M365 `Reasoning`)
    RESEARCH = "research"  # deep research (consumer `deep-research`)


@dataclass(slots=True)
class ModelInfo:
    """A concrete, edition-specific model/mode the service currently offers.
    Discovered from the edition's capability document, not hardcoded."""

    id: str                       # wire value: M365 `tone` / consumer `mode`
    title: str | None = None      # human label ("Think Deeper")
    description: str | None = None
    reasoning: bool = False       # a "thinking"/extended-reasoning model
    family: str | None = None     # e.g. "gpt-5.5"
    default: bool = False         # the service's default selection


@dataclass(slots=True)
class Citation:
    title: str | None = None
    url: str | None = None
    snippet: str | None = None


@dataclass(slots=True)
class Suggestion:
    text: str


@dataclass(slots=True)
class Throttling:
    used: int | None = None
    maximum: int | None = None

    @property
    def at_limit(self) -> bool:
        return (
            self.used is not None
            and self.maximum is not None
            and self.used >= self.maximum
        )


# ---- streamed events -------------------------------------------------------
@dataclass(slots=True)
class Delta:
    """An incremental chunk of the assistant's answer (always incremental, even
    for the cumulative SignalR protocol — the codec diffs it for you)."""

    text: str


@dataclass(slots=True)
class Progress:
    """A non-answer status event interleaved in the stream."""

    kind: str  # "search" | "thinking" | "tool" | "code" | "image" | "generic"
    text: str = ""


@dataclass(slots=True)
class Final:
    """Terminal event of a turn: the complete answer plus metadata."""

    text: str
    citations: list[Citation] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    throttling: Throttling | None = None
    conversation_id: str | None = None
    title: str | None = None


# A streamed turn yields these, terminated by exactly one `Final`.
Event = Delta | Progress | Final


@dataclass(slots=True)
class ConversationRef:
    """Handle to a conversation. `id` is always required; `signature` is the
    optional M365 per-conversation signature (usually empty); `extra` carries
    edition-specific bits (e.g. extra WS query params)."""

    id: str
    signature: str | None = None
    extra: dict = field(default_factory=dict)
