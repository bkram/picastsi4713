from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, jsonify, render_template, request

from .transmitter import ValidationError

bp = Blueprint("main", __name__)


def _manager():
    return current_app.transmitter_manager  # type: ignore[attr-defined]


@bp.get("/")
def index() -> str:
    manager = _manager()
    configs = manager.list_configs()
    return render_template("index.html", configs=configs)


@bp.get("/api/status")
def api_status() -> Response:
    manager = _manager()
    return jsonify(manager.current_status())


@bp.get("/api/configs")
def api_configs() -> Response:
    manager = _manager()
    return jsonify({"items": manager.list_configs()})


@bp.get("/api/configs/<path:name>")
def api_get_config(name: str) -> Response:
    manager = _manager()
    try:
        data = manager.read_config_struct(Path(name))
    except FileNotFoundError:
        return jsonify({"error": "Configuration not found"}), 404
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"name": name, "config": data})


@bp.post("/api/configs/<path:name>")
def api_save_config(name: str) -> Response:
    manager = _manager()
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict) or "config" not in payload:
        return jsonify({"error": "Invalid payload"}), 400
    config_payload = payload["config"]
    if not isinstance(config_payload, dict):
        return jsonify({"error": "Configuration must be an object"}), 400
    try:
        manager.write_config_struct(Path(name), config_payload)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "saved"})


@bp.post("/api/configs/<path:name>/apply")
def api_apply_config(name: str) -> Response:
    manager = _manager()
    try:
        status = manager.apply_config(Path(name))
    except FileNotFoundError:
        return jsonify({"error": "Configuration not found"}), 404
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(status)


@bp.post("/api/broadcast")
def api_toggle_broadcast() -> Response:
    manager = _manager()
    payload = request.get_json(force=True, silent=True)
    if not isinstance(payload, dict) or "enabled" not in payload:
        return jsonify({"error": "Invalid payload"}), 400
    enabled = bool(payload["enabled"])
    try:
        status = manager.set_broadcast(enabled)
    except ValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(status)


@bp.get("/events")
def sse_events() -> Response:
    manager = _manager()
    queue = manager.metrics_queue()

    def stream():
        try:
            while True:
                payload = queue.get()
                yield f"data: {json.dumps(payload)}\n\n"
        finally:
            manager.unregister_queue(queue)

    return Response(stream(), mimetype="text/event-stream")
