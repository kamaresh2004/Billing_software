from flask import Blueprint, jsonify
from sqlalchemy import text

from models import db

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.get("/healthz")
def api_healthz():
    try:
        db.session.execute(text("SELECT 1"))
        return jsonify({"status": "ok", "database": "ok"})
    except Exception:
        return jsonify({"status": "error", "database": "unavailable"}), 503
