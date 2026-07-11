"""Async research job REST API: submit returns immediately with a job id;
poll it for the structured-markdown report once it's done. Only mounted
(see `server.build_app`) when the active provider exposes a research backend
-- today, that's chatgpt only."""

from flask import jsonify, request

from ..application.research import get_research_job, submit_research


def register(app, store) -> None:
    @app.post("/v1/research")
    def create_job():
        body = request.get_json(silent=True) or {}
        query = (body.get("query") or "").strip()
        if not query:
            return jsonify({"error": {"message": "query is required"}}), 400
        job = submit_research(store, query, body.get("depth"))
        return jsonify(job.to_dict()), 202

    @app.get("/v1/research")
    def list_jobs():
        return jsonify({"data": [j.to_dict() for j in store.list_jobs()]})

    @app.get("/v1/research/<job_id>")
    def get_job(job_id):
        job = get_research_job(store, job_id)
        if job is None:
            return jsonify({"error": {"message": "job not found"}}), 404
        return jsonify(job.to_dict())

    @app.delete("/v1/research/<job_id>")
    def delete_job(job_id):
        if get_research_job(store, job_id) is None:
            return jsonify({"error": {"message": "job not found"}}), 404
        store.delete(job_id)
        return "", 204
