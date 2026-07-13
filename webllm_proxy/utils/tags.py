"""Emulated function calling over a plain web chat UI (chatgpt, copilot): a
`<tool>`/`<assistant>`/`<tool-response>` tag contract.

  build_preamble()   -> the first-turn system + tool-contract text
  parse_tool_calls() -> pull `<tool>` blocks back into OpenAI `tool_calls`
  format_tool_result() -> render a role:"tool" message as a `<tool-response>`

Flat `{"tool_name": ..., ...args}` JSON (which web models follow more reliably
than a nested/fenced block; a legacy ```tool_call fence is still salvaged).
There is NO native-channel interception here -- chatgpt's own recipient/tool
messages are ignored upstream; this only ever reads what the model wrote.
"""

import json
import re
import uuid

from .prompts import default_store

# ---- contract output patterns --------------------------------------------
_TOOL_TAG = re.compile(r"<tool>\s*(.*?)</tool>", re.S | re.I)
_OPEN_TOOL_TAG = re.compile(r"<tool>\s*(.*)\Z", re.S | re.I)
_ASSISTANT_TAG = re.compile(r"<assistant>\s*(.*?)</assistant>", re.S | re.I)
_OPEN_ASSISTANT_TAG = re.compile(r"<assistant>\s*(.*)\Z", re.S | re.I)
_FENCE = re.compile(r"```[ \t]*tool_call[ \t]*\r?\n?(.*?)```", re.S | re.I)
_ANYFENCE = re.compile(r"```[ \t]*[A-Za-z0-9_-]*[ \t]*\r?\n?(.*?)```", re.S)
_OPENFENCE = re.compile(r"```[ \t]*tool_call[ \t]*\r?\n?(.*)\Z", re.S | re.I)

_JSON_DEC = json.JSONDecoder()
_JUNK_TAILS = ("</tool>", "\\end_tool_call", "end_tool_call", "```", "`")


# ---- lenient JSON salvage -------------------------------------------------
def _load(s):
    if not isinstance(s, str):
        return None
    try:
        return json.loads(s.strip())
    except Exception:
        return None


def _is_call(j):
    if isinstance(j, dict):
        return ("name" in j or "tool_name" in j) or "tool_calls" in j or "tool_call" in j
    if isinstance(j, list):
        return bool(j) and all(isinstance(x, dict) and ("name" in x or "tool_name" in x) for x in j)
    return False


def _from_flat(d):
    if not isinstance(d, dict):
        return None
    name = d.get("tool_name") or d.get("name")
    if not name:
        return None
    a = d.get("arguments", d.get("args"))
    if isinstance(a, dict):
        args = a
    elif isinstance(a, str):
        loaded = _load(a)
        args = loaded if isinstance(loaded, dict) else {"value": a}
    else:
        args = {k: v for k, v in d.items() if k not in ("tool_name", "name", "arguments", "args")}
    return {"name": name, "arguments": args}


def _normalize(j):
    if isinstance(j, dict):
        if "tool_calls" in j:
            return _flatten(j["tool_calls"])
        if "tool_call" in j:
            return _flatten(j["tool_call"])
        if "name" in j or "tool_name" in j:
            one = _from_flat(j)
            return [one] if one else []
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


def _to_openai(calls):
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


def _salvage_call_json(s):
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


def _collect(blocks):
    calls = []
    for b in blocks:
        j = _load(b)
        if j is None:
            j = _salvage_call_json(b)
        if j is not None:
            calls += _normalize(j)
    return calls


# ---- preamble building ----------------------------------------------------
def _fn(t):
    if not isinstance(t, dict):
        return {}
    return t.get("function") if isinstance(t.get("function"), dict) else t


def _example_args(params):
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


def _tool_list_intro(count: int, names: str, exclusive: bool) -> str:
    if exclusive:
        return (
            f"You have exactly these {count} tool(s) and no others: {names}. "
            "These are the only actions available to you; there is no other way to "
            "run commands or read/write files. Each is defined below with its "
            "name, purpose, and the JSON Schema for its arguments (which go at the "
            "top level of the `<tool>` JSON)."
        )
    return (
        f"For this conversation you also have these {count} custom tool(s): "
        f"{names}, in addition to anything else you can normally do. Use one when "
        "it fits the user's request. Each is defined below with its name, purpose, "
        "and the JSON Schema for its arguments (which go at the top level of the "
        "`<tool>` JSON)."
    )


def build_preamble(
    system_text,
    tools,
    tool_choice="auto",
    forced_name=None,
    contract_prompt="webui_tool_contract",
    exclusive=True,
):
    """First-turn system prompt: the already-resolved configured system prompt
    text (or `None`/`""` -- the proxy ignores the client's own system messages
    entirely and only ever sends what the operator configured, see
    `utils.config.ProviderConfigBase.system_prompt_for`) + the tag tool
    contract (with each client tool's name/description/schema injected).

    `contract_prompt` selects which `prompts/system_prompts/<name>.md` frames the
    contract, and `exclusive` toggles the "these are the only actions available,
    there is no other way" claim -- providers whose model resists the default
    "connected to your real machine, no other tools exist" framing (e.g. copilot,
    which is itself safety-tuned against believing arbitrary user-asserted
    exclusive tool access, since it has real server-side tools of its own) can
    pass a milder variant of both without touching the default used by chatgpt."""
    has_sys = bool(system_text and system_text.strip())
    if not has_sys and not tools:
        return ""
    parts = []
    if has_sys:
        parts.append(system_text.strip())
    if tools:
        specs = [(_fn(t).get("name"), _fn(t)) for t in tools]
        specs = [(n, fn) for (n, fn) in specs if n]
        names = ", ".join(f"`{n}`" for (n, _) in specs)
        lines = [default_store.get(contract_prompt), "", "## Available tools"]
        lines.append(_tool_list_intro(len(specs), names, exclusive))
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
    """Render a role:"tool" message as a `<tool-response>` block (next user turn)."""
    tid = m.get("tool_call_id")
    name = m.get("name") or name_map.get(tid) or "tool"
    payload = {"tool_name": name, "ok": True, "result": _content_text(m)}
    return "<tool-response>\n" + json.dumps(payload, ensure_ascii=False) + "\n</tool-response>"


# ---- parsing --------------------------------------------------------------
def _extract_assistant(text):
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
    tblocks = _TOOL_TAG.findall(text)
    calls = _to_openai(_collect(tblocks)) if tblocks else []
    if not calls:
        return None
    la = _extract_assistant(text)
    return calls, (la if la is not None else _strip_tags(text))


def _try_unclosed_tool_tag(text):
    if "</tool>" in text:
        return None
    m = _OPEN_TOOL_TAG.search(text)
    if not m:
        return None
    parsed = _salvage_call_json(m.group(1))
    calls = _to_openai(_normalize(parsed)) if _is_call(parsed) else []
    if not calls:
        return None
    lead = text[: m.start()]
    la = _extract_assistant(lead)
    return calls, (la if la is not None else _strip_tags(lead))


def _try_closed_fence(text):
    blocks = _FENCE.findall(text)
    calls = _to_openai(_collect(blocks)) if blocks else []
    return (calls, _FENCE.sub("", text).strip()) if calls else None


def _try_unclosed_fence(text):
    m = _OPENFENCE.search(text)
    if not m:
        return None
    parsed = _salvage_call_json(m.group(1))
    calls = _to_openai(_normalize(parsed)) if _is_call(parsed) else []
    return (calls, text[: m.start()].strip()) if calls else None


def _try_any_fenced_block(text):
    fenced = [b for b in _ANYFENCE.findall(text) if _is_call(_load(b))]
    calls = _to_openai(_collect(fenced)) if fenced else []
    return (calls, "") if calls else None


def _try_bare_json(text):
    parsed = _load(text)
    calls = _to_openai(_normalize(parsed)) if _is_call(parsed) else []
    return (calls, "") if calls else None


_FALLBACK_TIERS = (
    _try_closed_tool_tag,
    _try_unclosed_tool_tag,
    _try_closed_fence,
    _try_unclosed_fence,
    _try_any_fenced_block,
    _try_bare_json,
)


def parse_tool_calls(text):
    """Extract (openai_tool_calls, leftover_text) from model output."""
    if not text:
        return [], ""
    for tier in _FALLBACK_TIERS:
        result = tier(text)
        if result is not None:
            return result
    la = _extract_assistant(text)
    return [], (la if la is not None else text)
