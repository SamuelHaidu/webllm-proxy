"""ChatGPT's conversation-continuity use case: map the client's stateless
OpenAI `messages[]` array onto ONE ongoing ChatGPT web conversation, sending
only the newest tail when the history is growing normally (continuing that
conversation) and re-priming with a full system+tool preamble when it isn't
(a fresh or diverging history -> a new ChatGPT conversation, since chatgpt has
no real "resume this exact message list" API of its own -- see docs/discovery/).
"""

import json

from ..prompts.loader import default_store
from ..strategies import tool_calling
from ..wire.openai import message_text

# OpenAI `reasoning_effort` (minimal/low/medium/high, plus 5.1's `none`) maps
# onto ChatGPT web's 4-level `thinking_effort` ladder (min/standard/extended/max).
_EFFORT_MAP = {
    "minimal": "min",
    "min": "min",
    "none": "min",
    "low": "standard",
    "standard": "standard",
    "medium": "extended",
    "extended": "extended",
    "high": "max",
    "max": "max",
}


def normalize_effort(body: dict) -> str | None:
    v = body.get("reasoning_effort")
    if not v:
        r = body.get("reasoning")
        v = r.get("effort") if isinstance(r, dict) else None
    if not v:
        return None
    return _EFFORT_MAP.get(str(v).strip().lower())


def message_signature(m: dict):
    """A hashable fingerprint of one message, used to detect whether the
    client's `messages[]` is a pure append (continuing this conversation) or
    has changed/shrunk (start a fresh one)."""
    role = m.get("role")
    if role == "assistant" and m.get("tool_calls"):
        return ("a_tc", json.dumps(m.get("tool_calls"), sort_keys=True, default=str))
    if role == "tool":
        return ("tool", m.get("tool_call_id"), message_text(m))
    return (role, message_text(m))


def _format_turns(msgs, name_map) -> str:
    out = []
    for m in msgs:
        role = m.get("role")
        if role == "user":
            out.append(message_text(m))
        elif role == "tool":
            out.append(tool_calling.format_tool_result(m, name_map))
    return "\n\n".join(t for t in out if t).strip()


class ConversationPlanner:
    """Tracks which prefix of the client's `messages[]` has already been sent
    upstream. One instance per browser session -- conversation continuity is
    memory, not a per-request concept."""

    def __init__(self):
        self._sigs: list = []

    def plan_turn(self, messages, tools, tool_choice, forced_name) -> tuple[str | None, bool]:
        """-> (text to send upstream, is_new_conversation)."""
        sigs = [message_signature(m) for m in messages]
        prev = self._sigs
        continuing = bool(prev) and len(sigs) > len(prev) and sigs[: len(prev)] == prev
        self._sigs = sigs
        name_map = tool_calling.tool_name_map(messages)
        if continuing:
            return _format_turns(messages[len(prev) :], name_map) or None, False

        system_text = "\n\n".join(message_text(m) for m in messages if m.get("role") == "system")
        preamble = tool_calling.build_preamble(system_text, tools, tool_choice, forced_name)
        last_user = max(
            (i for i, m in enumerate(messages) if m.get("role") == "user"), default=None
        )
        tail = messages[last_user:] if last_user is not None else messages
        body = _format_turns(tail, name_map)
        if not preamble:
            return (body or None), True
        framing = default_store.get("user_request_framing")
        text = (preamble + "\n\n" + framing + "\n\n" + body).strip()
        return (text or None), True
