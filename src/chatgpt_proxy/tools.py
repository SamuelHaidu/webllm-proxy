"""OpenAI-style function/tool calling, emulated over the ChatGPT web app.

ChatGPT's web backend has native tools (web search, python, ...) but exposes NO
client-facing function-calling API: you cannot hand it arbitrary tool schemas
and get structured `tool_calls` back the way the OpenAI API does. So we emulate
it with a prompt contract:

  1. build_preamble() injects the caller's system prompt + a description of each
     tool + an output contract ("emit a ```tool_call fenced block") on the first
     turn of the ChatGPT conversation.
  2. The model answers either with normal prose or with a `tool_call` block.
  3. parse_tool_calls() turns that block back into OpenAI `tool_calls`.
  4. When the caller returns a role:"tool" result, format_tool_result() renders
     it as the next user turn so the model can continue.

This is deterministic and needs no cooperation from the backend; reliability
rests on the model following the (very explicit) contract, which GPT-5 does.
"""
import json
import re
import uuid

# A fenced block whose info-string is exactly `tool_call` (the primary,
# unambiguous contract). Tolerates surrounding whitespace / CRLF.
_FENCE = re.compile(r"```[ \t]*tool_call[ \t]*\r?\n?(.*?)```", re.S | re.I)
# Any fenced block (fallback when the model tags it ```json or bare).
_ANYFENCE = re.compile(r"```[ \t]*[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.S)

_CONTRACT = (
    "# Tools\n"
    "You are connected to the user's real environment through the tools listed "
    "below. They are NOT hypothetical: when you call one it actually runs and you "
    "receive the real result. Never claim you cannot access or run these tools, "
    "and never fabricate a result.\n\n"
    "To call a tool, reply with a SINGLE fenced code block whose info string is "
    "exactly `tool_call` and nothing else (no prose before or after):\n"
    '```tool_call\n{"name": "<tool_name>", "arguments": { ...matching the schema... }}\n```\n'
    "Emit EXACTLY ONE tool call per reply — a single JSON object, never a list — "
    "then stop and wait for its result before doing anything else. Do not mix a "
    'tool_call block with prose. After each result arrives (a message beginning '
    '"Result from tool"), decide the next step: call one more tool, or, once the '
    "task is complete, give your final answer as ordinary text. If a tool returns "
    "an error, adjust and try again rather than giving up."
)


def _fn(t):
    """Accept either {'type':'function','function':{...}} or a bare {...}."""
    if not isinstance(t, dict):
        return {}
    return t.get("function") if isinstance(t.get("function"), dict) else t


def build_preamble(system_text, tools, tool_choice="auto", forced_name=None):
    """First-turn preamble = caller system prompt + the tool contract."""
    parts = []
    if system_text and system_text.strip():
        parts.append(system_text.strip())
    if tools:
        lines = [_CONTRACT, "", "## Available tools"]
        for t in tools:
            fn = _fn(t)
            name = fn.get("name")
            if not name:
                continue
            desc = (fn.get("description") or "").strip()
            params = fn.get("parameters") or {"type": "object", "properties": {}}
            lines.append(f"\n### {name}")
            if desc:
                lines.append(desc)
            lines.append("Parameters (JSON Schema): " + json.dumps(params, ensure_ascii=False))
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
        for tc in (m.get("tool_calls") or []):
            cid = tc.get("id")
            name = (tc.get("function") or {}).get("name")
            if cid:
                out[cid] = name
    return out


def _content_text(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "\n".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in c
        )
    return "" if c is None else str(c)


def format_tool_result(m, name_map):
    """Render a role:"tool" message as the next user turn for ChatGPT."""
    tid = m.get("tool_call_id")
    name = m.get("name") or name_map.get(tid) or "tool"
    head = f"Result from tool `{name}`" + (f" (call {tid})" if tid else "") + ":"
    return head + "\n" + _content_text(m)


# ---- parsing model output back into OpenAI tool_calls --------------------
def _load(s):
    if not isinstance(s, str):
        return None
    try:
        return json.loads(s.strip())
    except Exception:
        return None


def _is_call(j):
    if isinstance(j, dict):
        return ("name" in j and "arguments" in j) or "tool_calls" in j or "tool_call" in j
    if isinstance(j, list):
        return bool(j) and all(isinstance(x, dict) and "name" in x for x in j)
    return False


def _normalize(j):
    """Return a list of {'name', 'arguments'(dict)} from any accepted shape."""
    if isinstance(j, dict):
        if "tool_calls" in j:
            return _flatten(j["tool_calls"])
        if "tool_call" in j:
            return _flatten(j["tool_call"])
        if "name" in j:
            return [_one(j)]
        return []
    if isinstance(j, list):
        return _flatten(j)
    return []


def _flatten(v):
    if isinstance(v, list):
        out = []
        for x in v:
            out += _normalize(x) if isinstance(x, (list, dict)) else []
        return out
    if isinstance(v, dict):
        return _normalize(v)
    return []


def _one(d):
    name = d.get("name")
    args = d.get("arguments", d.get("args", {}))
    if isinstance(args, str):
        args = _load(args)
        if args is None:
            args = {}
    if not isinstance(args, dict):
        args = {"value": args}
    return {"name": name, "arguments": args}


def _to_openai(calls):
    out = []
    for c in calls:
        if not c.get("name"):
            continue
        out.append({
            "id": "call_" + uuid.uuid4().hex[:24],
            "type": "function",
            "function": {"name": c["name"],
                         "arguments": json.dumps(c.get("arguments") or {}, ensure_ascii=False)},
        })
    return out


def parse_tool_calls(text):
    """Extract tool calls from model output.
    Returns (openai_tool_calls, leftover_text)."""
    if not text:
        return [], ""
    blocks = _FENCE.findall(text)
    used_fence = bool(blocks)
    if not blocks:
        for b in _ANYFENCE.findall(text):
            if _is_call(_load(b)):
                blocks.append(b)
        if not blocks and _is_call(_load(text)):
            blocks = [text]
    calls = []
    for b in blocks:
        j = _load(b)
        if j is not None:
            calls += _normalize(j)
    openai_calls = _to_openai(calls)
    if used_fence:
        leftover = _FENCE.sub("", text).strip()
    else:
        leftover = "" if openai_calls else text
    return openai_calls, leftover
