"""Shared Flask app factory. Adds /health and mounts the active provider's
routes; everything provider-specific lives in the provider."""
from flask import Flask, jsonify
from flask_cors import CORS


def create_app(session, provider) -> Flask:
    app = Flask(__name__)
    CORS(app)

    @app.get("/health")
    def health():
        return jsonify({
            "provider": provider.name,
            "status": "running" if session.ready else "initializing",
            "ready": session.ready,
            "error": session.error,
        }), (200 if session.ready else 503)

    provider.register_routes(app, session)
    return app
