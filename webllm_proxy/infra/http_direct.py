"""urllib helper that bypasses any configured HTTP(S)_PROXY for loopback targets.

The gateway and the research CLI only ever talk to LOCAL per-provider proxies
(`127.0.0.1:510x`). Behind a corporate proxy (`HTTP_PROXY`/`HTTPS_PROXY` set so
the browser can reach the internet), the stdlib default opener would route that
loopback traffic through the corporate proxy too -- which cannot reach the
user's own machine, so every gateway forward / research poll would fail unless
the user also remembered to set `NO_PROXY=127.0.0.1,localhost`.

`urlopen` here forces a direct connection for loopback hosts and leaves genuine
remote URLs to honor the environment proxy as usual.
"""

from __future__ import annotations

import urllib.request
from typing import Any
from urllib.parse import urlsplit

# ProxyHandler({}) = no proxies at all; build_opener() (no args) = the stdlib
# default that reads getproxies()/env. We pick per-request by target host.
_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))

_LOOPBACK = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def is_loopback(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host in _LOOPBACK or host.startswith("127.")


def urlopen(req: urllib.request.Request | str, *, timeout: float) -> Any:
    """Like `urllib.request.urlopen`, but never sends a loopback request through
    an env-configured proxy. Raises/returns exactly as the stdlib does."""
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    opener = _DIRECT if is_loopback(url) else urllib.request.build_opener()
    return opener.open(req, timeout=timeout)
