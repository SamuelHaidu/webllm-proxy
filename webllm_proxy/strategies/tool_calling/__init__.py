"""Emulated OpenAI-style function/tool calling for a provider with no native
function-calling API. Two `ToolStrategy` implementations run in a fixed
fallback order: `native_channel` (ChatGPT already captured a native tool
call -- no prompt contract involved) first, then `agentclip` (the `<tool>` tag
contract parsed out of the visible reply) if that found nothing.

Everything used to build/parse the AgentClip contract (`build_preamble`,
`tool_name_map`, `format_tool_result`, `tool_names`, `parse_tool_calls`) is
re-exported here too, since callers need those independent of the
call-extraction fallback below.
"""

from .agentclip import (
    AgentClipStrategy,
    build_preamble,
    format_tool_result,
    parse_tool_calls,
    tool_name_map,
    tool_names,
)
from .native_channel import NativeChannelStrategy, native_to_openai

__all__ = [
    "build_preamble",
    "format_tool_result",
    "native_to_openai",
    "parse_tool_calls",
    "resolve_tool_calls",
    "tool_name_map",
    "tool_names",
]

# Order matters: native-channel capture is tried first; the tag contract is
# the fallback (see docs/discovery/2026-07-10-tool-calling.md).
_STRATEGIES = (NativeChannelStrategy(), AgentClipStrategy())


def resolve_tool_calls(content, native, allowed_names):
    """Run the strategies in order; return the first non-empty
    (openai_tool_calls, leftover_text)."""
    for strategy in _STRATEGIES:
        calls, leftover = strategy.extract_calls(content, native, allowed_names)
        if calls:
            return calls, leftover
    return [], content
