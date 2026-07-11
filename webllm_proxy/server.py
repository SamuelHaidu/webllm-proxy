"""Composition root: builds the Flask app, mounts the health check, hands
control to the active provider to register its own routes, and -- when that
provider exposes a research backend -- mounts the async research job API and
starts its background scheduler."""

from flask import Flask
from flask_cors import CORS

from .application.research import ResearchScheduler
from .http import health, research_routes
from .research.jobstore.memory import MemoryJobStore


def build_app(session, provider) -> Flask:
    app = Flask(__name__)
    CORS(app)
    health.register(app, session, provider)
    provider.register_routes(app, session)
    _mount_research(app, session, provider)
    return app


def _mount_research(app, session, provider) -> None:
    backend = provider.research_backend(session)
    if backend is None:
        return
    store = MemoryJobStore()
    ResearchScheduler(store, backend, session).start()
    research_routes.register(app, store)
