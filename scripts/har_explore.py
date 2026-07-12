#!/usr/bin/env python3
"""Explore a (large) HAR capture without dumping it into an LLM context window.

The whole HAR is parsed in-process, but every subcommand prints only compact,
secret-redacted summaries. Sensitive header values (Authorization, Cookie, ...)
and JSON body fields whose key looks like a credential are replaced with
``<redacted len=N>`` so the request/response *shape* stays visible.

Usage:
  har_explore.py FILE paths                    # unique host+path, with method/status/count
  har_explore.py FILE list [--url S] [--method M] [--status N] [--mime S] [--limit N]
  har_explore.py FILE show N [--max 4000] [--no-redact]
  har_explore.py FILE req  N [--max 8000] [--raw]      # request body only
  har_explore.py FILE resp N [--max 8000] [--raw]      # response body only
  har_explore.py FILE headers N [req|resp]             # headers only
  har_explore.py FILE grep PATTERN [--in url|req|resp|all] [--limit N]
  har_explore.py FILE keys N [req|resp]                # JSON key tree of a body
"""
import argparse
import json
import re
import sys
from urllib.parse import urlsplit

# --- redaction -------------------------------------------------------------
SECRET_HEADER = re.compile(
    r"(authorization|cookie|set-cookie|x-csrf|csrf|token|secret|api[-_]?key"
    r"|session|bearer|proxy-authorization|x-databricks-.*-token)",
    re.I,
)
SECRET_KEY = re.compile(
    r"(token|secret|password|passwd|authorization|cookie|api[-_]?key"
    r"|access[-_]?token|refresh[-_]?token|bearer|session[-_]?id|credential"
    r"|client[-_]?secret|private[-_]?key|signature|encrypted)",
    re.I,
)


def redact_val(v):
    s = v if isinstance(v, str) else json.dumps(v)
    return f"<redacted len={len(s)}>"


def redact_json(obj):
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and SECRET_KEY.search(k):
                out[k] = redact_val(v)
            else:
                out[k] = redact_json(v)
        return out
    if isinstance(obj, list):
        return [redact_json(x) for x in obj]
    return obj


def redact_body(text, mime, do_redact):
    if not text:
        return ""
    if not do_redact:
        return text
    if mime and "json" in mime:
        try:
            return json.dumps(redact_json(json.loads(text)), indent=2, ensure_ascii=False)
        except Exception:
            pass
    # non-JSON: redact obvious "key": "value" / key=value token pairs
    text = re.sub(
        r'("(?:[^"]*(?:token|secret|key|auth|session|cookie)[^"]*)"\s*:\s*)"[^"]*"',
        r'\1"<redacted>"', text, flags=re.I,
    )
    return text


def hdrs(header_list, do_redact=True):
    out = []
    for h in header_list or []:
        name, val = h.get("name", ""), h.get("value", "")
        if do_redact and SECRET_HEADER.search(name):
            val = redact_val(val)
        out.append((name, val))
    return out


# --- loading ---------------------------------------------------------------
def load(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)
    return data["log"]["entries"]


def redact_url(url):
    """Redact secret-looking query param values (access_token, sig, ...)."""
    sp = urlsplit(url)
    if not sp.query:
        return url
    from urllib.parse import parse_qsl, urlencode, urlunsplit
    q = []
    for k, v in parse_qsl(sp.query, keep_blank_values=True):
        if SECRET_KEY.search(k) or k.lower() in ("sig", "sp", "st", "se", "skoid"):
            v = f"<redacted len={len(v)}>"
        q.append((k, v))
    return urlunsplit((sp.scheme, sp.netloc, sp.path, urlencode(q, safe="<>= "), sp.fragment))


def short_url(url, n=70):
    sp = urlsplit(url)
    p = sp.path or "/"
    if sp.query:
        p += "?" + sp.query
    if len(p) > n:
        p = p[: n - 1] + "…"
    return p


def entry_mime(e):
    return (e.get("response", {}).get("content", {}) or {}).get("mimeType", "") or ""


def body_of(e, which):
    if which == "req":
        return (e["request"].get("postData", {}) or {}).get("text", ""), \
               (e["request"].get("postData", {}) or {}).get("mimeType", "")
    c = e["response"].get("content", {}) or {}
    return c.get("text", ""), c.get("mimeType", "")


# --- commands --------------------------------------------------------------
def cmd_paths(entries, a):
    from collections import Counter
    c = Counter()
    for e in entries:
        sp = urlsplit(e["request"]["url"])
        key = (e["request"]["method"], e["response"]["status"],
               sp.netloc + sp.path)
        c[key] += 1
    for (m, st, path), n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0][2])):
        print(f"{n:4d}  {m:6s} {st:3d}  {path}")
    print(f"\n{len(entries)} entries, {len(c)} unique method+status+path")


def match(e, a):
    if a.url and a.url.lower() not in e["request"]["url"].lower():
        return False
    if a.method and a.method.upper() != e["request"]["method"]:
        return False
    if a.status and int(a.status) != e["response"]["status"]:
        return False
    if a.mime and a.mime.lower() not in entry_mime(e).lower():
        return False
    return True


def cmd_list(entries, a):
    shown = 0
    for i, e in enumerate(entries):
        if not match(e, a):
            continue
        req, resp = e["request"], e["response"]
        size = (resp.get("content", {}) or {}).get("size", 0)
        print(f"[{i:4d}] {req['method']:5s} {resp['status']:3d} "
              f"{entry_mime(e)[:22]:22s} {size:>8} {short_url(req['url'])}")
        shown += 1
        if a.limit and shown >= a.limit:
            print(f"... (--limit {a.limit} reached)")
            break
    if not shown:
        print("no matching entries")


def cmd_show(entries, a):
    e = entries[a.n]
    req, resp = e["request"], e["response"]
    do_r = not a.no_redact
    print(f"### [{a.n}] {req['method']} {req['url']}")
    print(f"# started {e.get('startedDateTime','')}  time={e.get('time','?')}ms")
    print("\n--- REQUEST HEADERS ---")
    for k, v in hdrs(req.get("headers"), do_r):
        print(f"{k}: {v}")
    rb, rmime = body_of(e, "req")
    if rb:
        print(f"\n--- REQUEST BODY ({rmime}) ---")
        print(redact_body(rb, rmime, do_r)[: a.max])
    print(f"\n--- RESPONSE {resp['status']} {resp.get('statusText','')} ---")
    for k, v in hdrs(resp.get("headers"), do_r):
        print(f"{k}: {v}")
    sb, smime = body_of(e, "resp")
    if sb:
        print(f"\n--- RESPONSE BODY ({smime}) ---")
        print(redact_body(sb, smime, do_r)[: a.max])


def cmd_body(entries, a, which):
    e = entries[a.n]
    text, mime = body_of(e, which)
    if a.raw:
        sys.stdout.write(text)
        return
    print(redact_body(text, mime, True)[: a.max])


def cmd_headers(entries, a):
    e = entries[a.n]
    part = e["request"] if a.which == "req" else e["response"]
    for k, v in hdrs(part.get("headers"), True):
        print(f"{k}: {v}")


def cmd_grep(entries, a):
    rx = re.compile(a.pattern, re.I)
    hits = 0
    for i, e in enumerate(entries):
        blobs = []
        if a.in_ in ("url", "all"):
            blobs.append(("url", e["request"]["url"]))
        if a.in_ in ("req", "all"):
            blobs.append(("req", body_of(e, "req")[0]))
        if a.in_ in ("resp", "all"):
            blobs.append(("resp", body_of(e, "resp")[0]))
        for where, blob in blobs:
            if blob and rx.search(blob):
                m = rx.search(blob)
                s = max(0, m.start() - 40)
                snip = blob[s:m.end() + 40].replace("\n", " ")
                print(f"[{i:4d}] {where:4s} {e['request']['method']:5s} "
                      f"{short_url(e['request']['url'],40):40s} …{snip}…")
                hits += 1
                break
        if a.limit and hits >= a.limit:
            print(f"... (--limit {a.limit} reached)")
            break
    if not hits:
        print("no matches")


def key_tree(obj, prefix="", out=None, depth=0, maxdepth=6):
    if out is None:
        out = []
    if depth > maxdepth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            t = type(v).__name__
            red = " (redacted)" if SECRET_KEY.search(k) else ""
            print(f"{'  '*depth}{k}: {t}{red}")
            if not red:
                key_tree(v, out=out, depth=depth + 1, maxdepth=maxdepth)
    elif isinstance(obj, list):
        print(f"{'  '*depth}[{len(obj)}]")
        if obj:
            key_tree(obj[0], out=out, depth=depth + 1, maxdepth=maxdepth)
    return out


def cmd_keys(entries, a):
    e = entries[a.n]
    text, mime = body_of(e, a.which)
    if not text:
        print("(empty body)")
        return
    try:
        key_tree(json.loads(text))
    except Exception as ex:
        print(f"(not JSON: {ex}) first 300 chars:\n{text[:300]}")


# --- WebSocket frames (Chrome HAR: entry["_webSocketMessages"]) -------------
SIGNALR_TYPE = {
    1: "Invocation", 2: "StreamItem", 3: "Completion", 4: "StreamInvocation",
    5: "CancelInvocation", 6: "Ping", 7: "Close",
}
_RS = "\x1e"  # SignalR json-protocol record separator


def ws_entries(entries):
    return [(i, e) for i, e in enumerate(entries) if e.get("_webSocketMessages")]


def split_signalr(data):
    return [p for p in (data or "").split(_RS) if p.strip()]


def ws_label(rec_text):
    """(short label, parsed-obj-or-None) for one SignalR record."""
    try:
        obj = json.loads(rec_text)
    except Exception:
        return (rec_text[:60].replace("\n", " "), None)
    if not isinstance(obj, dict):
        return (str(obj)[:60], obj)
    t = obj.get("type")
    tag = SIGNALR_TYPE.get(t, f"type={t}" if t is not None else "handshake?")
    tgt = obj.get("target")
    if tgt:
        tag += f" {tgt}"
    return (tag, obj)


def cmd_ws(entries, a):
    wes = ws_entries(entries)
    if not wes:
        print("(no _webSocketMessages in this HAR)")
        return
    idx = a.n if a.n is not None else wes[0][0]
    e = entries[idx]
    msgs = e.get("_webSocketMessages") or []
    print(f"### entry [{idx}] {redact_url(e['request']['url'])}")
    print(f"# {len(msgs)} frames  (send=client->server, receive=server->client)")
    shown = 0
    for mi, m in enumerate(msgs):
        direction = m.get("type")
        if a.dir and direction != a.dir:
            continue
        data = m.get("data", "")
        recs = split_signalr(data)
        labels = " | ".join(ws_label(r)[0] for r in recs)
        arrow = "->S" if direction == "send" else "S->"
        print(f"[{mi:3d}] {arrow} bytes={len(data):6d} recs={len(recs):2d}  {labels[:110]}")
        shown += 1
        if a.limit and shown >= a.limit:
            print(f"... (--limit {a.limit} reached)")
            break


def cmd_wsshow(entries, a):
    e = entries[a.n]
    msgs = e.get("_webSocketMessages") or []
    if not msgs:
        print("(no _webSocketMessages on this entry)")
        return
    m = msgs[a.msg]
    data = m.get("data", "")
    do_r = not a.no_redact
    print(f"### entry [{a.n}] frame [{a.msg}] dir={m.get('type')} "
          f"opcode={m.get('opcode')} bytes={len(data)}")
    for ri, r in enumerate(split_signalr(data)):
        try:
            obj = json.loads(r)
            out = json.dumps(redact_json(obj) if do_r else obj, indent=2, ensure_ascii=False)
        except Exception:
            out = redact_body(r, "text", do_r)
        print(f"\n--- record {ri} ---")
        print(out[: a.max])


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("paths"); sp.set_defaults(fn=cmd_paths)

    sp = sub.add_parser("list")
    sp.add_argument("--url"); sp.add_argument("--method")
    sp.add_argument("--status"); sp.add_argument("--mime")
    sp.add_argument("--limit", type=int, default=60)
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("show")
    sp.add_argument("n", type=int); sp.add_argument("--max", type=int, default=4000)
    sp.add_argument("--no-redact", action="store_true")
    sp.set_defaults(fn=cmd_show)

    for name, which in (("req", "req"), ("resp", "resp")):
        sp = sub.add_parser(name)
        sp.add_argument("n", type=int)
        sp.add_argument("--max", type=int, default=8000)
        sp.add_argument("--raw", action="store_true")
        sp.set_defaults(fn=(lambda es, aa, w=which: cmd_body(es, aa, w)))

    sp = sub.add_parser("headers")
    sp.add_argument("n", type=int)
    sp.add_argument("which", nargs="?", choices=["req", "resp"], default="req")
    sp.set_defaults(fn=cmd_headers)

    sp = sub.add_parser("grep")
    sp.add_argument("pattern")
    sp.add_argument("--in", dest="in_", choices=["url", "req", "resp", "all"], default="all")
    sp.add_argument("--limit", type=int, default=40)
    sp.set_defaults(fn=cmd_grep)

    sp = sub.add_parser("keys")
    sp.add_argument("n", type=int)
    sp.add_argument("which", nargs="?", choices=["req", "resp"], default="resp")
    sp.set_defaults(fn=cmd_keys)

    sp = sub.add_parser("ws", help="list WebSocket frames of an entry (SignalR-aware)")
    sp.add_argument("n", type=int, nargs="?", default=None, help="entry index (default: first WS entry)")
    sp.add_argument("--dir", choices=["send", "receive"], default=None)
    sp.add_argument("--limit", type=int, default=100)
    sp.set_defaults(fn=cmd_ws)

    sp = sub.add_parser("wsshow", help="show one WebSocket frame's records (redacted)")
    sp.add_argument("n", type=int, help="entry index")
    sp.add_argument("msg", type=int, help="frame index within the entry")
    sp.add_argument("--max", type=int, default=8000)
    sp.add_argument("--no-redact", action="store_true")
    sp.set_defaults(fn=cmd_wsshow)

    a = p.parse_args()
    entries = load(a.file)
    a.fn(entries, a)


if __name__ == "__main__":
    main()
