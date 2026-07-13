"""Flask app factory. Holds no provider logic; just wires the routes over the
`{name: Provider}` map."""

from __future__ import annotations

from flask import Flask
from flask_cors import CORS

from .routes import register_routes


def build_app(providers: dict) -> Flask:
    app = Flask(__name__)
    CORS(app)
    register_routes(app, providers)
    return app
