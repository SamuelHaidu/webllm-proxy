"""The research feature: schedule a web-research job, poll it, get back a
structured-markdown report. `backends/` holds the swappable research engines
(`ResearchBackend`); `jobstore/` holds the swappable job storage (`JobStore`).
Orchestration lives in `application/research.py`; the REST API in
`http/research_routes.py`."""
