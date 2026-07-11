"""Research job shapes: a request (what to research) and a job (its lifecycle
as it runs). Plain dataclasses -- no Flask, no browser, no backend-specific
fields; a `ResearchBackend` receives the request and reports progress/errors
back through the job it's given."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class ResearchRequest:
    """What to research. `depth` is a free-form hint (e.g. "quick"/"deep")
    passed to whichever backend runs the job; a backend may ignore it."""

    query: str
    depth: str | None = None


@dataclass
class ResearchJob:
    """One research job's full lifecycle state, as tracked by a `JobStore`."""

    request: ResearchRequest
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: JobStatus = JobStatus.QUEUED
    backend: str | None = None
    report: str | None = None
    error: str | None = None
    progress: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None

    def add_progress(self, note: str) -> None:
        self.progress.append(note)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status.value,
            "query": self.request.query,
            "depth": self.request.depth,
            "backend": self.backend,
            "report": self.report,
            "error": self.error,
            "progress": list(self.progress),
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
