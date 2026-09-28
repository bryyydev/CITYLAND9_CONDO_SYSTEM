"""Serve the built React app (frontend/dist) at /app/ from this same local server.

Same origin as the API, so no CORS is needed. Unknown /app/... paths return
index.html so React Router can handle deep links such as /app/billing.
"""
import os

from flask import Blueprint, send_from_directory


def make_spa_blueprint(dist_dir):
    bp = Blueprint("react_app", __name__)

    @bp.get("/app")
    @bp.get("/app/")
    @bp.get("/app/<path:path>")
    def react_app(path=""):
        index = os.path.join(dist_dir, "index.html")
        if not os.path.isfile(index):
            return ("The React frontend has not been built yet. Run:  cd frontend  then  npm run build", 503,
                    {"Content-Type": "text/plain; charset=utf-8"})
        if path and os.path.isfile(os.path.join(dist_dir, path)):
            # send_from_directory rejects paths outside dist_dir (no path traversal).
            return send_from_directory(dist_dir, path)
        response = send_from_directory(dist_dir, "index.html")
        response.headers["Cache-Control"] = "no-cache"
        return response

    return bp
