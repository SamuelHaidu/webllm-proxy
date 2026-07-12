"""The gateway/CLI only talk to LOCAL per-provider proxies; behind a corporate
proxy those loopback calls must NOT be routed through HTTP_PROXY. Guards
`infra.http_direct`: loopback detection + the direct opener carrying no proxies."""

import urllib.request

from webllm_proxy.infra import http_direct


def test_is_loopback_true():
    for u in (
        "http://127.0.0.1:5100/v1/models",
        "http://localhost:5103/health",
        "http://[::1]:5100/",
        "http://0.0.0.0:5102/x",
        "http://127.9.9.9:1/",
    ):
        assert http_direct.is_loopback(u), u


def test_is_loopback_false():
    for u in (
        "https://chatgpt.com/backend-api",
        "http://example.com:5100/",
        "https://dbc-xxxx.cloud.databricks.com/ajax-api",
    ):
        assert not http_direct.is_loopback(u), u


def test_direct_opener_ignores_env_proxy(monkeypatch):
    # With a corporate proxy in the env, a normal opener picks it up...
    monkeypatch.setenv("HTTP_PROXY", "http://corp-proxy:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://corp-proxy:8080")
    default_proxies = [
        h
        for h in urllib.request.build_opener().handlers
        if isinstance(h, urllib.request.ProxyHandler)
    ]
    assert any(h.proxies for h in default_proxies), "default opener should honor env proxy"
    # ...but the module's direct opener carries no active proxy handler, so a
    # loopback request goes straight to the local proxy, never the corporate one.
    direct_proxies = [
        h for h in http_direct._DIRECT.handlers if isinstance(h, urllib.request.ProxyHandler)
    ]
    assert all(not h.proxies for h in direct_proxies)
