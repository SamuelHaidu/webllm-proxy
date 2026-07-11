"""The AgentClip tag-contract strategy: emulates OpenAI-style function calling
over a web chat UI that has no such API, by prompting the model into a
`<tool>`/`<assistant>`/`<tool-response>` protocol and parsing its reply back
into OpenAI `tool_calls`.

  1. build_preamble() injects the caller's system prompt + a description of
     each tool + the tag-based output contract, on the first turn.
  2. The model answers with `<assistant>` text and/or a `<tool>` JSON block.
  3. parse_tool_calls() turns any `<tool>` block back into OpenAI `tool_calls`.
  4. When the caller returns a role:"tool" result, format_tool_result() renders
     it as a `<tool-response>` block (the next user turn) so the model
     continues.

Ported from AgentClip's `system_prompt.md` (FLAT `{"tool_name": ..., ...args}`
JSON), which ChatGPT web models follow more reliably than a fenced
```tool_call``` block (still accepted as a legacy fallback below). Validated
with `gpt-5-mini`; `auto`/`gpt-5-5` refuse the contract outright (correctly
flag the injected "SYSTEM INSTRUCTIONS" block as unauthoritative user text) --
see docs/discovery/2026-07-10-tool-calling.md Update 4.
"""

import json
import re

from ...prompts.loader import default_store
from . import _shared

# ---- contract output patterns --------------------------------------------
# Primary: <tool> ... </tool> (a single flat JSON object) and <assistant> text.
_TOOL_TAG = re.compile(r"<tool>\s*(.*?)</tool>", re.S | re.I)
_OPEN_TOOL_TAG = re.compile(r"<tool>\s*(.*)\Z", re.S | re.I)  # unclosed <tool>
_ASSISTANT_TAG = re.compile(r"<assistant>\s*(.*?)</assistant>", re.S | re.I)
_OPEN_ASSISTANT_TAG = re.compile(r"<assistant>\s*(.*)\Z", re.S | re.I)  # unclosed <assistant>

# Legacy fallback: a ```tool_call fenced JSON block (closed / unclosed / any).
_FENCE = re.compile(r"```[ \t]*tool_call[ \t]*\r?\n?(.*?)```", re.S | re.I)
_ANYFENCE = re.compile(r"```[ \t]*[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.S)
_OPENFENCE = re.compile(r"```[ \t]*tool_call[ \t]*\r?\n?(.*)\Z", re.S | re.I)


def _fn(t):
    """Accept either {'type':'function','function':{...}} or a bare {...}."""
    if not isinstance(t, dict):
        return {}
    return t.get("function") if isinstance(t.get("function"), dict) else t


def _example_args(params):
    """A tiny illustrative arg object from a JSON Schema (property names ->
    placeholder values), so each tool shows a concrete `<tool>` example."""
    props = (params or {}).get("properties") or {}
    ex = {}
    for k, spec in list(props.items())[:4]:
        t = (spec or {}).get("type")
        ex[k] = {
            "string": "…",
            "integer": 0,
            "number": 0,
            "boolean": True,
            "array": [],
            "object": {},
        }.get(t, "…")
    return ex


def build_preamble(system_text, tools, tool_choice="auto", forced_name=None):
    """First-turn system prompt: the caller's system text + the tool contract,
    framed as one clearly-delimited system-instructions block. ChatGPT web has no
    separate system role over this transport, so we send it as the head of the
    first message, ahead of (and marked as outranking) the user request."""
    has_sys = bool(system_text and system_text.strip())
    if not has_sys and not tools:
        return ""
    parts = [default_store.get("system_header")]
    if has_sys:
        parts.append(system_text.strip())
    if tools:
        specs = [(_fn(t).get("name"), _fn(t)) for t in tools]
        specs = [(n, fn) for (n, fn) in specs if n]
        names = ", ".join(f"`{n}`" for (n, _) in specs)
        lines = [default_store.get("tool_contract"), "", "## Available tools"]
        lines.append(
            f"You have exactly these {len(specs)} tool(s) and no others: {names}. "
            "These are the only actions available to you; there is no other way to "
            "run commands or read/write files. Each is defined below with its "
            "name, purpose, and the JSON Schema for its arguments (which go at the "
            "top level of the `<tool>` JSON)."
        )
        for name, fn in specs:
            desc = (fn.get("description") or "").strip()
            params = fn.get("parameters") or {"type": "object", "properties": {}}
            lines.append(f"\n### {name}")
            if desc:
                lines.append(desc)
            lines.append("Arguments (JSON Schema): " + json.dumps(params, ensure_ascii=False))
            example = {"tool_name": name, **_example_args(params)}
            lines.append("Example: <tool>" + json.dumps(example, ensure_ascii=False) + "</tool>")
        if forced_name:
            lines.append(f"\nYou MUST call the tool `{forced_name}` on this turn.")
        elif tool_choice == "required":
            lines.append("\nYou MUST call one of the tools on this turn.")
        parts.append("\n".join(lines))
    return "\n\n".join(parts).strip()


def tool_name_map(messages):
    """Map assistant tool_call ids -> function name (to label results)."""
    out = {}
    for m in messages:
        for tc in m.get("tool_calls") or []:
            cid = tc.get("id")
            name = (tc.get("function") or {}).get("name")
            if cid:
                out[cid] = name
    return out


def tool_names(tools):
    """Set of function names the client declared."""
    out = set()
    for t in tools or []:
        n = _fn(t).get("name")
        if n:
            out.add(n)
    return out


def _content_text(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return "" if c is None else str(c)


def format_tool_result(m, name_map):
    """Render a role:"tool" message as a `<tool-response>` block (next user turn),
    matching the contract the model was given."""
    tid = m.get("tool_call_id")
    name = m.get("name") or name_map.get(tid) or "tool"
    payload = {"tool_name": name, "ok": True, "result": _content_text(m)}
    return "<tool-response>\n" + json.dumps(payload, ensure_ascii=False) + "\n</tool-response>"


def _extract_assistant(text):
    """Joined text of any `<assistant>` blocks, or None if there are none. Falls
    back to a trailing UNCLOSED `<assistant>` (the model stopped generating
    before writing the closing tag, e.g. hit its token limit) so the literal
    tag never leaks into the visible reply."""
    blocks = _ASSISTANT_TAG.findall(text)
    if blocks:
        return "\n".join(b.strip() for b in blocks).strip()
    if "</assistant>" not in text:
        m = _OPEN_ASSISTANT_TAG.search(text)
        if m:
            return m.group(1).strip()
    return None


def _strip_tags(text):
    t = _TOOL_TAG.sub("", text)
    t = re.sub(r"</?assistant>", "", t, flags=re.I)
    return t.strip()


def _try_closed_tool_tag(text):
    """1) `<tool> ... </tool>` blocks — the primary contract."""
    tblocks = _TOOL_TAG.findall(text)
    calls = _shared.to_openai(_shared.collect(tblocks)) if tblocks else []
    if not calls:
        return None
    la = _extract_assistant(text)
    return calls, (la if la is not None else _strip_tags(text))


def _try_unclosed_tool_tag(text):
    """2) UNCLOSED `<tool>` (model emitted the JSON then stopped)."""
    if "</tool>" in text:
        return None
    m = _OPEN_TOOL_TAG.search(text)
    if not m:
        return None
    parsed = _shared.salvage_call_json(m.group(1))
    calls = _shared.to_openai(_shared.normalize(parsed)) if _shared.is_call(parsed) else []
    if not calls:
        return None
    lead = text[: m.start()]
    la = _extract_assistant(lead)
    return calls, (la if la is not None else _strip_tags(lead))


def _try_closed_fence(text):
    """3) Legacy: a properly closed ```tool_call fence."""
    blocks = _FENCE.findall(text)
    calls = _shared.to_openai(_shared.collect(blocks)) if blocks else []
    return (calls, _FENCE.sub("", text).strip()) if calls else None


def _try_unclosed_fence(text):
    """4) Legacy: an UNCLOSED ```tool_call fence."""
    m = _OPENFENCE.search(text)
    if not m:
        return None
    parsed = _shared.salvage_call_json(m.group(1))
    calls = _shared.to_openai(_shared.normalize(parsed)) if _shared.is_call(parsed) else []
    return (calls, text[: m.start()].strip()) if calls else None


def _try_any_fenced_block(text):
    """5) Any other fenced block that looks like a call (```json or bare)."""
    fenced = [b for b in _ANYFENCE.findall(text) if _shared.is_call(_shared.load(b))]
    calls = _shared.to_openai(_shared.collect(fenced)) if fenced else []
    return (calls, "") if calls else None


def _try_bare_json(text):
    """6) A bare top-level JSON object/array."""
    parsed = _shared.load(text)
    calls = _shared.to_openai(_shared.normalize(parsed)) if _shared.is_call(parsed) else []
    return (calls, "") if calls else None


# Tried in order; the first tier to recognize a call wins.
_FALLBACK_TIERS = (
    _try_closed_tool_tag,
    _try_unclosed_tool_tag,
    _try_closed_fence,
    _try_unclosed_fence,
    _try_any_fenced_block,
    _try_bare_json,
)


def parse_tool_calls(text):
    """Extract tool calls from model output, trying each contract shape
    `_FALLBACK_TIERS` describes, in order.
    Returns (openai_tool_calls, leftover_text)."""
    if not text:
        return [], ""
    for tier in _FALLBACK_TIERS:
        result = tier(text)
        if result is not None:
            return result
    # 7) No tool call — content is the <assistant> text, else the whole reply.
    la = _extract_assistant(text)
    return [], (la if la is not None else text)


class AgentClipStrategy:
    """`ToolStrategy`: parse the `<tool>` tag contract out of the visible
    reply text. Ignores `native`/`allowed_names` -- this strategy only ever
    looks at what the model wrote, not at ChatGPT's native recipient channel."""

    def extract_calls(self, content, native, allowed_names):
        return parse_tool_calls(content)
