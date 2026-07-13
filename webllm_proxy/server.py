"""Composition root: boot every enabled provider's browser session, then serve
one Flask app (one host/port) over all of them."""

from __future__ import annotations

import atexit
import logging
import sys

from .http import build_app
from .providers import build_enabled
from .utils import tokens
from .utils.config import Config

log = logging.getLogger(__name__)


def serve(config: Config) -> int:
    tokens.configure_profiles(config.tokenizer_profiles())
    tokens.configure_model_profiles(config.model_tokenizer_overrides())
    names = config.enabled_providers()
    if not names:
        print("FATAL: no providers enabled in the config file", file=sys.stderr)
        return 2

    try:
        providers = build_enabled(config)
    except Exception as e:
        print(f"FATAL: {e}", file=sys.stderr)
        return 2

    for name, p in providers.items():
        print(f"[{name}] booting browser (headless={p.session.headless}) ...")
        p.start()

    ready_any = False
    for name, p in providers.items():
        if p.wait_ready(120):
            ready_any = True
            print(f"[{name}] ready")
        else:
            print(f"[{name}] WARNING: not ready ({p.error})", file=sys.stderr)

    if not ready_any:
        print("FATAL: no provider became ready", file=sys.stderr)
        for p in providers.values():
            p.close()
        return 1

    app = build_app(providers)
    atexit.register(lambda: [p.close() for p in providers.values()])

    host, port = config.server.host, config.server.port
    print(f"[webllm-proxy] ready on http://{host}:{port}")
    print("  GET  /v1/models             merged; ids namespaced <provider>__<slug>")
    print("  POST /v1/chat/completions   routed by model prefix")
    print("  GET  /health                aggregated readiness")
    try:
        app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
    except KeyboardInterrupt:
        pass
    finally:
        for p in providers.values():
            p.close()
    return 0
