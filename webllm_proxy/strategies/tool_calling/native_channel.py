"""The native-channel strategy: ChatGPT's web backend routes a message to a
non-"all" recipient when the model invokes a tool through its own mechanism
(the sentinel/native tool-call flow `sse.py` captures), rather than emitting
the AgentClip tags. `native_to_openai` converts those captures into OpenAI
`tool_calls`, keeping only ones addressed to a client-declared tool -- see
docs/discovery/2026-07-10-tool-calling.md Update 3.

Special case: `container.exec` -- ChatGPT's OWN native code sandbox. Thinking
models (e.g. gpt-5-4-t-mini) route real shell work to this recipient (a
`bash -lc <cmd>` executor ChatGPT auto-runs in ITS OWN container, NOT the
user's machine) and then hallucinate results from it. We hijack those calls to
a client-declared shell tool so the command actually runs where the user
expects (see docs/discovery/2026-07-10-tool-calling.md Update 5).
"""

import re

from . import _shared

_NATIVE_CODE_RECIPIENTS = {"container.exec", "container_exec", "python_exec"}
# Client tool names (in preference order) we treat as "the shell" to hijack to.
_SHELL_TOOL_NAMES = (
    "bash",
    "shell",
    "sh",
    "run_bash",
    "run_command",
    "run_shell",
    "runbash",
    "run_terminal_cmd",
    "execute",
    "exec",
    "terminal",
    "run",
)
# Strip a leading `bash -lc` / `sh -c` / ... wrapper off a container.exec payload.
_SHELL_WRAPPER = re.compile(r"^\s*(?:bash|sh|zsh|/bin/bash|/bin/sh)\s+-l?c\s+", re.I)
_MIN_QUOTED_LEN = 2  # shortest possible matching-quote pair: "" or ''


def _shell_tool(allowed_names):
    """First client-declared tool that looks like a shell, or None."""
    if not allowed_names:
        return None
    for cand in _SHELL_TOOL_NAMES:
        if cand in allowed_names:
            return cand
    return None


def _container_command(raw):
    """Extract the shell command from a `container.exec` payload. The captured
    text is usually `bash -lc <command>` (sometimes the command is quoted, or the
    payload is a JSON object/array); return the bare command string."""
    s = (raw or "").strip()
    if not s:
        return ""
    # JSON shapes: {"cmd": [...]}/{"command": "..."} or a bare ["bash","-lc","..."]
    j = _shared.load(s) or _shared.salvage_call_json(s)
    if isinstance(j, dict):
        c = j.get("command") or j.get("cmd") or j.get("bash") or j.get("script")
        if isinstance(c, list):
            c = c[-1] if c else ""
        if isinstance(c, str) and c.strip():
            s = c.strip()
    elif isinstance(j, list) and j:
        s = str(j[-1]).strip()
    m = _SHELL_WRAPPER.match(s)
    if m:
        s = s[m.end() :].strip()
        is_quoted_pair = len(s) >= _MIN_QUOTED_LEN and s[0] in "\"'" and s[-1] == s[0]
        if is_quoted_pair:
            s = s[1:-1]
    return s.strip()


def _allowed(name, allowed_names):
    return bool(name) and (allowed_names is None or name in allowed_names)


def _convert_container_exec(raw, allowed_names):
    """`container.exec` (ChatGPT's own code sandbox) hijacked to a
    client-declared shell tool so a thinking model's sandbox commands run on
    the real machine instead of being dropped."""
    shell = _shell_tool(allowed_names)
    cmd = _container_command(raw)
    return [{"name": shell, "arguments": {"command": cmd}}] if shell and cmd else []


def _convert_declared_tool(name, raw, allowed_names):
    """The recipient IS a client-declared tool: `raw` is that tool's
    arguments -- unless it's itself a flat contract-shaped call addressed to a
    generic recipient, in which case unwrap it instead."""
    args = _shared.load(raw)
    if not isinstance(args, dict):
        salv = _shared.salvage_call_json(raw)
        args = salv if isinstance(salv, dict) else {}
    if "tool_name" in args or ("name" in args and "arguments" in args):
        flat = _shared.from_flat(args)
        if flat and _allowed(flat["name"], allowed_names):
            return [flat]
    return [{"name": name, "arguments": args}]


def _convert_unrecognized_recipient(raw, allowed_names):
    """The recipient isn't a client tool (e.g. the model routed to a generic
    recipient like `tool_call`/`functions`): `raw` may itself be a
    contract-shaped call (or a list of them), leniently parsed."""
    j = _shared.load(raw)
    if not _shared.is_call(j):
        j = _shared.salvage_call_json(raw)
    return [c for c in _shared.normalize(j) if _allowed(c.get("name"), allowed_names)]


def _convert_one(nc, allowed_names):
    name = nc.get("name")
    raw = nc.get("arguments") or ""
    if name in _NATIVE_CODE_RECIPIENTS:
        return _convert_container_exec(raw, allowed_names)
    if _allowed(name, allowed_names):
        return _convert_declared_tool(name, raw, allowed_names)
    return _convert_unrecognized_recipient(raw, allowed_names)


def native_to_openai(native, allowed_names=None):
    """Convert native-channel tool-call captures (from the SSE: each a dict
    {"name": recipient, "arguments": raw-JSON-text}) into OpenAI tool_calls,
    keeping only calls whose name is a client-declared tool (so ChatGPT's own
    native tools like `web`/`python` are ignored). JSON is parsed leniently
    (salvage) throughout to tolerate a slightly-truncated stream; see
    `_convert_one` for the three recipient shapes handled."""
    simple = [c for nc in (native or []) for c in _convert_one(nc, allowed_names)]
    return _shared.to_openai(simple)


class NativeChannelStrategy:
    """`ToolStrategy`: tool calls ChatGPT already captured via its own native
    recipient channel. Ignores `content` -- and, matching this codebase's
    existing behavior, discards any accompanying visible text (`leftover`
    is always "") once a native call is found; keeps only the single first
    call (`[:1]`), matching the "one tool call per turn" contract."""

    def extract_calls(self, content, native, allowed_names):
        return native_to_openai(native, allowed_names)[:1], ""
