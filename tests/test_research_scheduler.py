"""ResearchScheduler + MemoryJobStore unit tests (no browser): job lifecycle,
oldest-queued-first ordering, failure handling, and the real background
thread -- against a fake backend."""

import time

from webllm_proxy.application.research import ResearchScheduler, submit_research
from webllm_proxy.domain.research import JobStatus
from webllm_proxy.research.jobstore.memory import MemoryJobStore


class _FakeBackend:
    name = "fake"

    def __init__(self, fn):
        self._fn = fn

    def available(self, session):
        return True

    def run(self, request, *, session, on_progress):
        on_progress("started")
        return self._fn(request)


def _run_one_synchronously(store, backend, session=None):
    """Drive the scheduler's own job-selection + run logic once, without the
    background thread (deterministic for tests)."""
    scheduler = ResearchScheduler(store, backend, session)
    job = scheduler._next_queued()
    assert job is not None
    scheduler._run_job(job)
    return job


def test_successful_job_produces_a_report():
    store = MemoryJobStore()
    job = submit_research(store, "what is toolz")
    ran = _run_one_synchronously(store, _FakeBackend(lambda req: f"# Report\n{req.query}"))
    assert ran.id == job.id
    assert ran.status == JobStatus.SUCCEEDED
    assert ran.report == "# Report\nwhat is toolz"
    assert ran.backend == "fake"
    assert ran.progress == ["started"]
    assert ran.started_at is not None
    assert ran.finished_at is not None


def test_failed_job_records_the_error():
    store = MemoryJobStore()
    submit_research(store, "boom")

    def boom(_req):
        raise RuntimeError("upstream exploded")

    ran = _run_one_synchronously(store, _FakeBackend(boom))
    assert ran.status == JobStatus.FAILED
    assert ran.error == "upstream exploded"
    assert ran.report is None


def test_oldest_queued_job_runs_first():
    store = MemoryJobStore()
    first = submit_research(store, "first")
    time.sleep(0.01)
    submit_research(store, "second")
    scheduler = ResearchScheduler(store, _FakeBackend(lambda r: "ok"), None)
    picked = scheduler._next_queued()
    assert picked.id == first.id


def test_get_and_list_and_delete_round_trip():
    store = MemoryJobStore()
    job = submit_research(store, "q")
    assert store.get(job.id) is job
    assert job in store.list_jobs()
    store.delete(job.id)
    assert store.get(job.id) is None


def test_scheduler_thread_processes_a_queued_job():
    store = MemoryJobStore()
    job = submit_research(store, "threaded")
    scheduler = ResearchScheduler(store, _FakeBackend(lambda r: "done"), None, poll_interval_s=0.01)
    scheduler.start()
    try:
        deadline = time.time() + 2
        while time.time() < deadline:
            if store.get(job.id).status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                break
            time.sleep(0.02)
        assert store.get(job.id).status == JobStatus.SUCCEEDED
        assert store.get(job.id).report == "done"
    finally:
        scheduler.stop()
