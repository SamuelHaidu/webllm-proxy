"""In-process `JobStore` (a dict behind a lock). Jobs don't survive a
restart -- fine for a local dev proxy; swap in a persistent store (same
shape) if that matters for your setup."""

import threading

from ...domain.research import ResearchJob


class MemoryJobStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, ResearchJob] = {}

    def put(self, job: ResearchJob) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> ResearchJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> list[ResearchJob]:
        with self._lock:
            return list(self._jobs.values())

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
