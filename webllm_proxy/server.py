"""Composition root: builds the Flask app, mounts the health check, and hands
control to the active provider to register its own routes."""

from flask import Flask
from flask_cors import CORS

from .http import health


def build_app(session, provider) -> Flask:
    app = Flask(__name__)
    CORS(app)
    health.register(app, session, provider)
    provider.register_routes(app, session)
    return app
