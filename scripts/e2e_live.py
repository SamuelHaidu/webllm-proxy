"""Reproducible end-to-end LIVE test: boot the real unified server against
real logged-in browser sessions and drive the OpenAI-SDK smoke suite through
every provider that is actually usable, then tear everything down cleanly.

What it does:
  1. preflight each requested provider's login profile (skip un-logged-in ones)
  2. write an ephemeral webllm-proxy.yaml (OS temp dir, never the repo) on a
     dedicated port, enabling only the providers that passed preflight
  3. launch `python -m webllm_proxy serve` as a subprocess
  4. poll GET /health until each provider is ready (or times out)
  5. GET /v1/models, pick one representative model per ready provider
  6. run tests/smoke_openai_sdk.py (SDK client) against the live server
  7. optionally hit chatgpt__research once (--include-research)
  8. always: terminate the server + confirm no orphan Chrome; exit = pytest's code

Usage:
    uv run python scripts/e2e_live.py                       # all three providers
    uv run python scripts/e2e_live.py --providers chatgpt
    uv run python scripts/e2e_live.py --include-research --timeout 200

Secrets discipline: never prints full workspace URLs, tokens, or cookies.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from webllm_proxy.utils import env  # noqa: E402
from webllm_proxy.utils.process import profile_chrome_pids  # noqa: E402

ALL_PROVIDERS = ("chatgpt", "databricks", "copilot")


# ---- helpers --------------------------------------------------------------
def _profile_dir(provider: str) -> Path:
    return env.data_dir(f"{provider}-proxy") / "profile"


def _has_login(provider: str) -> bool:
    p = _profile_dir(provider)
    # A real logged-in persistent context has a Default/ subdir with cookies.
    return p.is_dir() and any(p.iterdir())


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _get_json(url: str, timeout: float = 10.0):
    # /health returns 503 while any provider is still booting (its top-level
    # status is AND-of-all-providers); the body still carries the per-provider
    # readiness dict we need, so read it even on an HTTP error status.
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _load_real_config() -> dict:
    real = _ROOT / "webllm-proxy.yaml"
    if real.exists():
        return yaml.safe_load(real.read_text(encoding="utf-8")) or {}
    return {}


def _build_config(providers: list[str], port: int) -> dict:
    cfg = _load_real_config()
    cfg.setdefault("server", {})
    cfg["server"]["host"] = "127.0.0.1"
    cfg["server"]["port"] = port
    prov = cfg.setdefault("providers", {})
    for name in ALL_PROVIDERS:
        block = prov.setdefault(name, {})
        block["enabled"] = name in providers
    # Backfill databricks workspace URL from env if the yaml lacks one.
    if "databricks" in providers and not prov["databricks"].get("workspace_url"):
        ws = os.environ.get("DATABRICKS_PROXY_URL", "")
        if ws:
            prov["databricks"]["workspace_url"] = ws
    return cfg


def _write_tmp_config(cfg: dict) -> Path:
    fd, path = tempfile.mkstemp(prefix="webllm-e2e-", suffix=".yaml")
    os.close(fd)
    Path(path).write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return Path(path)


def _poll_health(origin: str, providers: list[str], timeout: float) -> dict[str, bool]:
    """Poll /health (root, not /v1) until every provider is either ready or has a
    permanent boot error (no point waiting the full timeout on one that already
    failed to authenticate), or the timeout elapses."""
    ready: dict[str, bool] = dict.fromkeys(providers, False)
    deadline = time.time() + timeout
    last_print = 0.0
    while time.time() < deadline:
        try:
            _status, body = _get_json(f"{origin}/health", timeout=5)
        except (urllib.error.URLError, ConnectionError, OSError):
            time.sleep(2)
            continue
        per = body.get("providers", {})
        for p in providers:
            ready[p] = bool(per.get(p, {}).get("ready"))
        # A provider is "settled" once it's ready or has surfaced an error.
        pending = [p for p in providers if not ready[p] and not per.get(p, {}).get("error")]
        if not pending:
            return ready
        now = time.time()
        if now - last_print > 8:
            print(f"  ...waiting for readiness: {pending}")
            last_print = now
        time.sleep(2)
    return ready


def _discover_models(base: str, ready: list[str]) -> list[str]:
    try:
        _status, body = _get_json(f"{base}/models", timeout=10)
    except Exception as e:
        print(f"  could not fetch /v1/models: {e}")
        return []
    picked: dict[str, str] = {}
    for m in body.get("data", []):
        mid = m.get("id", "")
        provider, sep, slug = mid.partition("__")
        if not sep or provider not in ready or slug == "research":
            continue
        picked.setdefault(provider, mid)
    return list(picked.values())


def _run_research(base: str, timeout: float) -> None:
    print("\n== research (chatgpt__research) ==")
    body = json.dumps(
        {
            "model": "chatgpt__research",
            "messages": [
                {"role": "user", "content": "In 3 sentences, what is the uv Python tool?"}
            ],
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        content = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        print(f"  research report chars: {len(content)}  {'OK' if content.strip() else 'EMPTY'}")
    except Exception as e:
        print(f"  research call failed: {e}")


# ---- main -----------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--providers", default=",".join(ALL_PROVIDERS), help="comma list (default: all)"
    )
    ap.add_argument("--port", type=int, default=15100, help="server port (default: 15100)")
    ap.add_argument("--timeout", type=float, default=150.0, help="readiness wait seconds")
    ap.add_argument("--include-research", action="store_true", help="also hit chatgpt__research")
    args = ap.parse_args(argv)

    host = "127.0.0.1"
    origin = f"http://{host}:{args.port}"
    base = f"{origin}/v1"
    requested = [p.strip() for p in args.providers.split(",") if p.strip() in ALL_PROVIDERS]

    # 1. preflight logins
    providers = []
    for p in requested:
        if _has_login(p):
            providers.append(p)
        else:
            print(f"[skip] {p}: no login profile -- run `uv run webllm-proxy login --provider {p}`")
    if not providers:
        print("\nNo logged-in providers to test. Nothing to do.")
        return 3
    print(f"[preflight] testing providers: {providers}")

    # 3. free-port check
    if not _port_free(host, args.port):
        print(
            f"FATAL: port {args.port} already in use (another run?). Use --port.", file=sys.stderr
        )
        return 2

    # 2. ephemeral config
    cfg = _build_config(providers, args.port)
    if "databricks" in providers and not cfg["providers"]["databricks"].get("workspace_url"):
        print("[skip] databricks: no workspace_url (yaml/DATABRICKS_PROXY_URL) -- dropping it")
        providers.remove("databricks")
        cfg["providers"]["databricks"]["enabled"] = False
        if not providers:
            print("Nothing left to test.")
            return 3
    tmp_cfg = _write_tmp_config(cfg)
    log_path = Path(tempfile.gettempdir()) / f"webllm-e2e-server-{args.port}.log"
    print(f"[config] {tmp_cfg}\n[server log] {log_path}")

    proc = None
    log_fh = log_path.open("w", encoding="utf-8")
    try:
        # 4. launch server
        proc = subprocess.Popen(
            [sys.executable, "-m", "webllm_proxy", "serve", "--config-file", str(tmp_cfg)],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(_ROOT),
        )
        print(f"[server] launched pid={proc.pid}; booting browsers ...")

        # 5. poll health
        ready_map = _poll_health(origin, providers, args.timeout)
        if proc.poll() is not None:
            print(
                f"FATAL: server exited early (code {proc.returncode}); see {log_path}",
                file=sys.stderr,
            )
            return 1
        ready = [p for p, r in ready_map.items() if r]
        failed = [p for p, r in ready_map.items() if not r]
        print(f"[health] ready={ready}  not-ready={failed}")
        if not ready:
            print(f"FATAL: no provider became ready (see {log_path})", file=sys.stderr)
            return 1

        # 6. discover models
        models = _discover_models(base, ready)
        if not models:
            print("FATAL: no models discovered for ready providers", file=sys.stderr)
            return 1
        print(f"[models] testing: {models}")

        # 7. run the SDK smoke suite live
        sub_env = dict(os.environ)
        sub_env["WEBLLM_PROXY_SMOKE"] = "1"
        sub_env["WEBLLM_PROXY_BASE_URL"] = base
        sub_env["WEBLLM_PROXY_MODELS"] = ",".join(models)
        print("\n== running tests/smoke_openai_sdk.py against the live server ==\n")
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/smoke_openai_sdk.py", "-v", "--no-header"],
            cwd=str(_ROOT),
            env=sub_env,
        )

        # 8. optional research
        if args.include_research and "chatgpt" in ready:
            _run_research(base, timeout=max(args.timeout, 600))

        return result.returncode
    finally:
        # 9. teardown
        log_fh.close()
        if proc is not None and proc.poll() is None:
            print("\n[teardown] stopping server ...")
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        # confirm no orphan Chrome per tested profile
        time.sleep(1)
        orphans = {p: profile_chrome_pids(str(_profile_dir(p))) for p in providers}
        leftover = {p: pids for p, pids in orphans.items() if pids}
        if leftover:
            print(f"[teardown] WARNING: leftover Chrome pids: {leftover}")
        else:
            print("[teardown] clean: no orphan Chrome processes")
        with contextlib.suppress(OSError):
            tmp_cfg.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
