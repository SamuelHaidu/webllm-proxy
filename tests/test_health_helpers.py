"""http.health unit tests (no browser, no Flask app running): `requires_ready`,
and `release_lock_when_done` -- which exists specifically to avoid
double-acquiring a lock the caller already holds (`threading.Lock` isn't
reentrant; a live smoke test caught this exact deadlock during Phase A5 when
the wrapper used to call `lock.acquire()` itself -- see
docs/refactor/PROGRESS.md)."""

import threading

from flask import Flask

from webllm_proxy.http.health import release_lock_when_done, requires_ready


def test_release_lock_when_done_does_not_reacquire_already_held_lock():
    lock = threading.Lock()
    lock.acquire()  # simulate the caller having already acquired it
    items = list(release_lock_when_done(lock, iter(["a", "b", "c"])))
    assert items == ["a", "b", "c"]
    assert lock.locked() is False  # released exactly once, by the wrapper


def test_release_lock_when_done_releases_even_if_generator_raises():
    lock = threading.Lock()
    lock.acquire()

    def boom():
        yield "x"
        raise RuntimeError("boom")

    gen = release_lock_when_done(lock, boom())
    assert next(gen) == "x"
    try:
        next(gen)
        raised = False
    except RuntimeError:
        raised = True
    assert raised
    assert lock.locked() is False


def test_requires_ready_short_circuits_when_not_ready():
    class FakeSession:
        ready = False

    calls = []

    @requires_ready(FakeSession(), lambda: {"error": "nope"})
    def view():
        calls.append(1)
        return "ok"

    app = Flask(__name__)
    with app.app_context():
        _body, status = view()
    assert status == 503
    assert calls == []


def test_requires_ready_calls_through_when_ready():
    class FakeSession:
        ready = True

    @requires_ready(FakeSession(), lambda: {"error": "nope"})
    def view():
        return "ok"

    assert view() == "ok"
