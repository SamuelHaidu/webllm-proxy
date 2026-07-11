"""Cross-cutting route helpers shared by every provider's HTTP surface: the
`/health` check, the `@requires_ready` readiness gate, and
`release_lock_when_done` for a generator response body that must keep an
already-acquired lock held for its whole streaming lifetime, not just until
the route function itself returns."""

import functools

from flask import jsonify


def register(app, session, provider) -> None:
    @app.get("/health")
    def health():
        body = {
            "provider": provider.name,
            "status": "running" if session.ready else "initializing",
            "ready": session.ready,
            "error": session.error,
        }
        return jsonify(body), (200 if session.ready else 503)


def requires_ready(session, error_body):
    """Route decorator: short-circuit with a 503 (`error_body()`'s JSON) before
    the wrapped handler runs, if `session` isn't authenticated yet. `error_body`
    is a zero-arg callable because the different wire protocols shape "not
    ready" differently -- pass `wire.openai.unavailable_error` or
    `wire.anthropic.unavailable_error`."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            if not session.ready:
                return jsonify(error_body()), 503
            return fn(*args, **kwargs)

        return wrapper

    return decorator


def release_lock_when_done(lock, generator):
    """Wrap a generator response body so `lock` -- already acquired by the
    caller, e.g. right before `session.submit(...)` -- is released only once
    this generator is exhausted or raises, instead of when the route function
    itself returns. For an SSE response that must keep the single shared
    browser serialized with other turns for as long as the client is still
    receiving bytes. Do NOT call `lock.acquire()` here too: the caller already
    holds it, and `threading.Lock` isn't reentrant."""
    try:
        yield from generator
    finally:
        lock.release()
