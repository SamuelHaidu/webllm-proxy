"""Research job orchestration: create/read jobs in a `JobStore`, and
`ResearchScheduler`, a background thread that drains queued jobs one at a
time through the resolved `ResearchBackend`.

Serialized, not parallel: research shares the one browser with interactive
chat turns (`transport/browser.py`'s `BrowserSession` processes one job at a
time regardless), so running two research jobs -- or a research job and a
chat turn -- concurrently would mean two `page.goto`/composer interactions
racing on the same page. The async job API means callers aren't blocked
either way (submit returns immediately; poll for the result); a dedicated
browser tab/context for research is a documented follow-up, not implemented
here (would need `BrowserSession` to support more than one concurrent
`_active` job).
"""

import logging
import threading
import time

from ..domain.research import JobStatus, ResearchJob, ResearchRequest

log = logging.getLogger(__name__)


def submit_research(store, query: str, depth: str | None = None) -> ResearchJob:
    job = ResearchJob(request=ResearchRequest(query=query, depth=depth))
    store.put(job)
    return job


def get_research_job(store, job_id: str) -> ResearchJob | None:
    return store.get(job_id)


class ResearchScheduler:
    """One instance per server process (there's one shared browser to
    serialize against). Runs in a daemon thread; polls `store` for the oldest
    queued job and runs it to completion through `backend` before picking up
    the next one."""

    def __init__(self, store, backend, session, *, poll_interval_s: float = 0.5):
        self._store = store
        self._backend = backend
        self._session = session
        self._poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="research-scheduler", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            job = self._next_queued()
            if job is None:
                time.sleep(self._poll_interval_s)
                continue
            self._run_job(job)

    def _next_queued(self) -> ResearchJob | None:
        queued = [j for j in self._store.list_jobs() if j.status == JobStatus.QUEUED]
        return min(queued, key=lambda j: j.created_at, default=None)

    def _run_job(self, job: ResearchJob) -> None:
        job.status = JobStatus.RUNNING
        job.started_at = time.time()
        job.backend = self._backend.name
        self._store.put(job)
        try:
            report = self._backend.run(
                job.request, session=self._session, on_progress=job.add_progress
            )
            job.report = report
            job.status = JobStatus.SUCCEEDED
        except Exception as e:
            log.exception("research job %s failed", job.id)
            job.error = str(e)
            job.status = JobStatus.FAILED
        finally:
            job.finished_at = time.time()
            self._store.put(job)
