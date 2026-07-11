"""Lenient JSON parsing shared by both tool-calling strategies: model output is
never guaranteed to be clean JSON (truncation, stray closing tags, a fenced
block instead of a bare object), so every entry point into "does this text
contain a tool call" goes through `_salvage_call_json` rather than a plain
`json.loads`.
"""

import json
import uuid

_JSON_DEC = json.JSONDecoder()
# Bogus "closers" models emit instead of a proper terminator after the JSON.
_JUNK_TAILS = ("</tool>", "\\end_tool_call", "end_tool_call", "```", "`")


def load(s):
    if not isinstance(s, str):
        return None
    try:
        return json.loads(s.strip())
    except Exception:
        return None


def is_call(j):
    if isinstance(j, dict):
        return ("name" in j or "tool_name" in j) or "tool_calls" in j or "tool_call" in j
    if isinstance(j, list):
        return bool(j) and all(isinstance(x, dict) and ("name" in x or "tool_name" in x) for x in j)
    return False


def from_flat(d):
    """Normalize one call object to {'name', 'arguments'(dict)}. Accepts the flat
    AgentClip shape ({'tool_name', ...args}) and the nested legacy shape
    ({'name', 'arguments': {...}})."""
    if not isinstance(d, dict):
        return None
    name = d.get("tool_name") or d.get("name")
    if not name:
        return None
    a = d.get("arguments", d.get("args"))
    if isinstance(a, dict):
        args = a
    elif isinstance(a, str):
        loaded = load(a)
        args = loaded if isinstance(loaded, dict) else {"value": a}
    else:
        args = {k: v for k, v in d.items() if k not in ("tool_name", "name", "arguments", "args")}
    return {"name": name, "arguments": args}


def normalize(j):
    """Return a list of {'name', 'arguments'(dict)} from any accepted shape."""
    if isinstance(j, dict):
        if "tool_calls" in j:
            return flatten(j["tool_calls"])
        if "tool_call" in j:
            return flatten(j["tool_call"])
        if "name" in j or "tool_name" in j:
            one = from_flat(j)
            return [one] if one else []
        return []
    if isinstance(j, list):
        return flatten(j)
    return []


def flatten(v):
    if isinstance(v, list):
        out = []
        for x in v:
            out += normalize(x) if isinstance(x, (list, dict)) else []
        return out
    if isinstance(v, dict):
        return normalize(v)
    return []


def to_openai(calls):
    out = []
    for c in calls:
        if not c.get("name"):
            continue
        out.append(
            {
                "id": "call_" + uuid.uuid4().hex[:24],
                "type": "function",
                "function": {
                    "name": c["name"],
                    "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False),
                },
            }
        )
    return out


def salvage_call_json(s):
    """Best-effort parse of a tool-call JSON object out of `s`, tolerating an
    unclosed block: trailing junk after the object (a stray `</tool>`/```, a bogus
    closer, prose), a missing closing brace, or a dangling string. Returns the
    parsed object/list, or None.

    Uses raw_decode (which ignores trailing data) so a complete-but-followed-by-
    junk object parses directly; if the object was left unclosed we retry with a
    few appended braces (and a closing quote) — models sometimes drop the final
    brace on large `write` payloads."""
    start = s.find("{")
    if start < 0:
        start = s.find("[")
    if start < 0:
        return None
    frag = s[start:].strip()
    changed = True
    while changed:
        changed = False
        for j in _JUNK_TAILS:
            if frag.endswith(j):
                frag = frag[: -len(j)].rstrip()
                changed = True
    for close in ("", "}", "}}", '"}', '"}}', "]"):
        try:
            obj, _ = _JSON_DEC.raw_decode(frag + close)
            return obj
        except Exception:
            continue
    return None


def collect(blocks):
    calls = []
    for b in blocks:
        j = load(b)
        if j is None:
            j = salvage_call_json(b)
        if j is not None:
            calls += normalize(j)
    return calls
