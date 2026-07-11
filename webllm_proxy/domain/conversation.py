"""The one conversation-turn shape worth a dataclass.

Deliberately minimal: no `Message`/`ToolCall`/`ToolResult` wrapping the
client's raw OpenAI `messages[]`/`tool_calls[]` JSON. Those are read directly
(a handful of `.get()` accessors in `application/chat.py` and
`strategies/tool_calling/`) and re-serialized directly (`wire/openai.py`) --
introducing DTOs to parse-then-reconstruct the exact same JSON on its way
through would be a mapper for its own sake, not a real seam. `ChatTurn` earns
its place because it's an actual internal concept (one job for the browser
worker), not a copy of wire JSON.
"""

from dataclasses import dataclass


@dataclass
class ChatTurn:
    """One browser-worker job payload for a chat-style provider: the text to
    send, which model/effort to force, and whether this starts a fresh
    upstream conversation. (Databricks doesn't use this -- its job payload
    already *is* the exact upstream request body, passed straight through.)"""

    message: str
    model: str | None
    new_conversation: bool
    effort: str | None = None
