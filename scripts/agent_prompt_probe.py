"""Minimal probe for the ChatGPT agent-tag prompt.

Question it answers: given `prompts/chatgpt_agent.md` + a `<request>`, does the
live ChatGPT web model (a) FOLLOW the protocol (emit a proper `<read_file>` /
other tag as its first step), (b) REFUSE the contract, or (c) HALLUCINATE (make
up main.py's contents / fake a `<result>` instead of asking to read the file)?

One turn is enough to tell them apart: the model cannot write a correct test
without first reading main.py, so a well-behaved agent must emit `<read_file>`.

Sends the prompt as a single *user* message (no tools) so we test exactly the
gentle prompt, without the proxy's `system_header.md`/tool_contract wrapper.

Usage:
  uv run python scripts/agent_prompt_probe.py health
  uv run python scripts/agent_prompt_probe.py models
  uv run python scripts/agent_prompt_probe.py ask [MODEL]
Env: WEBLLM_BASE (default http://127.0.0.1:5102)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAX_OUT = 4000

BASE = os.environ.get("WEBLLM_BASE", "http://127.0.0.1:5102")
_ROOT = Path(__file__).resolve().parent.parent
PROMPT_FILE = _ROOT / "webllm_proxy" / "prompts" / "chatgpt_agent.md"
PROJECT_ROOT = _ROOT / "docs" / "discovery" / "project_test"
SKIP = {
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".ruff_cache",
    ".pytest_cache",
    ".pi",
    "dist",
}

REQUEST = (
    "Read main.py, then write a test suite for the functions in it using "
    "Python's stdlib unittest, and finally tell me the exact command to run "
    "the tests."
)


def _req(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"raw": raw}
    except urllib.error.URLError as e:
        return 0, {"error": str(e)}


def _tree(root: Path) -> str:
    lines = [root.name + "/"]

    def walk(d: Path, prefix: str) -> None:
        kids = sorted(
            (p for p in d.iterdir() if p.name not in SKIP),
            key=lambda p: (p.is_file(), p.name),
        )
        for p in kids:
            lines.append(f"{prefix}{p.name}{'/' if p.is_dir() else ''}")
            if p.is_dir():
                walk(p, prefix + "  ")

    walk(root, "  ")
    return "\n".join(lines)


def _ask(model: str) -> None:
    prompt = PROMPT_FILE.read_text(encoding="utf-8").replace(
        "<<PROJECT_TREE>>", _tree(PROJECT_ROOT)
    )
    user = f"{prompt}\n\n<request>{REQUEST}</request>"
    status, data = _req(
        "POST",
        "/v1/chat/completions",
        {"model": model, "stream": False, "messages": [{"role": "user", "content": user}]},
    )
    print(f"HTTP {status}  requested_model={model}")
    if status != 200:
        print(json.dumps(data, indent=2)[:2000])
        return
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message", {})
    reasoning = (msg.get("reasoning_content") or "").strip()
    if reasoning:
        print("\n===== reasoning (truncated) =====")
        print(reasoning[:1500])
    print("\n===== content =====")
    print(msg.get("content") or "(empty)")
    print("\n===== meta =====")
    print(f"finish={ch.get('finish_reason')}  real_model={data.get('model')}")


# --- minimal harness (parse the tags + execute them in project_test) --------

_PATTERNS = {
    "read_file": re.compile(r"<read_file\b([^>]*?)/?>", re.S),
    "create_file": re.compile(r"<create_file\b([^>]*)>(.*?)</create_file>", re.S),
    "edit_file": re.compile(r"<edit_file\b([^>]*)>(.*?)</edit_file>", re.S),
    "bash": re.compile(r"<bash>(.*?)</bash>", re.S),
    "find": re.compile(r"<find\b([^>]*)>(.*?)</find>", re.S),
    "search": re.compile(r"<search\b([^>]*)>(.*?)</search>", re.S),
}


def _attr(attrs: str, name: str) -> str | None:
    m = re.search(rf'{name}\s*=\s*"([^"]*)"', attrs or "")
    return m.group(1) if m else None


def parse_action(reply: str) -> dict | None:
    best = None
    for kind, pat in _PATTERNS.items():
        m = pat.search(reply)
        if m and (best is None or m.start() < best[1].start()):
            best = (kind, m)
    if best is None:
        return None
    kind, m = best
    if kind == "read_file":
        return {
            "kind": kind,
            "path": _attr(m.group(1), "path"),
            "lines": _attr(m.group(1), "lines"),
        }
    if kind == "create_file":
        return {"kind": kind, "path": _attr(m.group(1), "path"), "body": m.group(2)}
    if kind == "edit_file":
        inner = m.group(2)
        old = re.search(r"<old>(.*?)</old>", inner, re.S)
        new = re.search(r"<new>(.*?)</new>", inner, re.S)
        return {
            "kind": kind,
            "path": _attr(m.group(1), "path"),
            "old": old.group(1) if old else None,
            "new": new.group(1) if new else None,
        }
    if kind == "bash":
        return {"kind": kind, "cmd": m.group(1).strip()}
    if kind == "find":
        return {"kind": kind, "glob": m.group(2).strip()}
    return {"kind": kind, "regex": m.group(2).strip(), "path": _attr(m.group(1), "path")}


def _safe(root: Path, path: str) -> Path:
    p = (root / path).resolve()
    if p != root.resolve() and root.resolve() not in p.parents:
        raise ValueError(f"path escapes project root: {path}")
    return p


def execute(action: dict, root: Path) -> str:
    kind = action["kind"]
    try:
        if kind == "read_file":
            text = _safe(root, action["path"]).read_text(encoding="utf-8")
            if action.get("lines"):
                a, _, b = action["lines"].partition("-")
                text = "\n".join(text.splitlines()[int(a) - 1 : int(b or a)])
            return text[:MAX_OUT]
        if kind == "create_file":
            p = _safe(root, action["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            body = action["body"]
            p.write_text(body, encoding="utf-8")
            return f"created {action['path']} ({len(body)} bytes)"
        if kind == "edit_file":
            p = _safe(root, action["path"])
            text = p.read_text(encoding="utf-8")
            old = action["old"]
            if not old or text.count(old) != 1:
                return (
                    f"error: <old> must match exactly once (count={text.count(old) if old else 0})"
                )
            p.write_text(text.replace(old, action["new"] or "", 1), encoding="utf-8")
            return f"edited {action['path']}"
        if kind == "bash":
            r = subprocess.run(
                ["bash", "-c", action["cmd"]],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            return f"$ {action['cmd']}\n[exit {r.returncode}]\n{(r.stdout + r.stderr)[:MAX_OUT]}"
        if kind == "find":
            matches = sorted(str(x.relative_to(root)) for x in root.glob(action["glob"]))
            return "\n".join(matches[:200]) or "(no matches)"
        if kind == "search":
            base = _safe(root, action["path"]) if action.get("path") else root
            rx = re.compile(action["regex"])
            files = [base] if base.is_file() else base.rglob("*")
            out = []
            for f in files:
                if not f.is_file() or any(s in f.parts for s in SKIP):
                    continue
                try:
                    lines = f.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeDecodeError):
                    continue
                for i, ln in enumerate(lines, 1):
                    if rx.search(ln):
                        out.append(f"{f.relative_to(root)}:{i}:{ln}")
                if len(out) >= 200:
                    break
            return "\n".join(out[:200]) or "(no matches)"
    except Exception as e:  # noqa: BLE001 - probe: report any executor failure back to the model
        return f"error: {e}"
    return "error: unknown action"


def _loop(model: str, max_steps: int = 8) -> None:
    root = PROJECT_ROOT
    prompt = PROMPT_FILE.read_text(encoding="utf-8").replace("<<PROJECT_TREE>>", _tree(root))
    history = [{"role": "user", "content": f"{prompt}\n\n<request>{REQUEST}</request>"}]
    for step in range(1, max_steps + 1):
        status, data = _req(
            "POST", "/v1/chat/completions", {"model": model, "stream": False, "messages": history}
        )
        if status != 200:
            print(f"HTTP {status}: {json.dumps(data)[:800]}")
            return
        reply = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        history.append({"role": "assistant", "content": reply})
        print(f"\n########## turn {step}: model reply ##########\n{reply}")
        action = parse_action(reply)
        if not action:
            print("\n>>> no action tag -> FINAL answer. loop done.")
            return
        result = execute(action, root)
        print(f"\n----- harness ran {action['kind']} -> result -----\n{result}")
        history.append({"role": "user", "content": f"<result>\n{result}\n</result>"})
    print("\n>>> hit max steps without a final answer")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "ask"
    if cmd == "health":
        print(*_req("GET", "/health"))
    elif cmd == "models":
        status, data = _req("GET", "/v1/models")
        ids = [m.get("id") for m in data.get("data", [])] if isinstance(data, dict) else data
        print(status, json.dumps(ids, indent=2))
    elif cmd == "ask":
        _ask(sys.argv[2] if len(sys.argv) > 2 else "gpt-5")
    elif cmd == "loop":
        _loop(sys.argv[2] if len(sys.argv) > 2 else "gpt-5-5")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
