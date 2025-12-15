#!/usr/bin/env python3
"""
Lightweight Flask API + HTML frontend for PiCastSI4713.

Features:
- In-memory status (RT/PS/bank/timestamps) shared with the main process.
- Config CRUD within the cfg directory (list/read/write/delete).
- Minimal HTML page with live RT/PS updates and config editor.
"""

from __future__ import annotations

import json
import os
import threading
import time
import logging
from typing import Any, Dict, List, NoReturn, Optional, cast

from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    request,
    send_from_directory,
)  # pyright: ignore[reportMissingImports]

logger = logging.getLogger(__name__)


class StatusBus:
    """Thread-safe in-memory status shared between TX loop and Flask."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Dict[str, object] = {
            "config_path": None,
            "ps": [],
            "ps_current": None,
            "rt_text": "",
            "rt_bank": 0,
            "rt_updated_at": None,
            "pending_config": None,
        }

    def set_config_path(self, path: str) -> None:
        with self._lock:
            self._state["config_path"] = os.path.abspath(path)

    def update_ps(self, ps_list: List[str]) -> None:
        with self._lock:
            self._state["ps"] = list(ps_list)

    def update_ps_current(self, ps_text: str) -> None:
        with self._lock:
            self._state["ps_current"] = ps_text

    def update_rt(self, text: str, bank: int) -> None:
        with self._lock:
            self._state["rt_text"] = text
            self._state["rt_bank"] = int(bank) & 1
            self._state["rt_updated_at"] = time.time()

    def request_config_switch(self, path: str) -> None:
        with self._lock:
            self._state["pending_config"] = os.path.abspath(path)

    def current_config_path(self) -> Optional[str]:
        with self._lock:
            val = self._state.get("config_path")
            return str(val) if isinstance(val, str) else None

    def pop_pending_config(self) -> Optional[str]:
        with self._lock:
            path = self._state.get("pending_config")
            self._state["pending_config"] = None
            return path if isinstance(path, str) else None

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            data = dict(self._state)
        # Convert timestamp to ISO-ish string for convenience
        ts = data.get("rt_updated_at")
        if isinstance(ts, (int, float)):
            data["rt_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        return data


def _safe_cfg_path(cfg_dir: str, name: str) -> str:
    if "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        abort(400, "invalid config name")
    if not name.endswith(".json"):
        abort(400, "config must end with .json")
    path = os.path.abspath(os.path.join(cfg_dir, name))
    if not path.startswith(os.path.abspath(cfg_dir) + os.sep):
        abort(400, "invalid config path")
    return path


def _list_cfgs(cfg_dir: str) -> List[str]:
    try:
        return sorted(
            f
            for f in os.listdir(cfg_dir)
            if os.path.isfile(os.path.join(cfg_dir, f))
            and f.endswith(".json")
            and not f.startswith(".")
            and f != "state.json"
        )
    except FileNotFoundError:
        return []


def _write_atomic(path: str, data: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _load_config_dict(path: str) -> Dict[str, object]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            abort(400, "config root must be a mapping")
        return data
    except FileNotFoundError:
        abort(404, "config not found")
    except Exception as exc:
        logger.error("Failed to load config %s: %s", path, exc)
        abort(400, "failed to parse config")
    raise RuntimeError("unreachable")


def _dump_config_dict(path: str, data: Dict[str, object]) -> None:
    _write_atomic(path, json.dumps(data, indent=2, sort_keys=True))


def _validate_power_range_dict(cfg: Dict[str, object]) -> None:
    rf = cfg.get("rf") if isinstance(cfg, dict) else None
    if not isinstance(rf, dict):
        return
    power = rf.get("power")
    if power is None:
        return
    try:
        pval = int(power)
    except Exception:
        return
    if pval < 88 or pval > 120:
        abort(400, "rf.power must be between 88 and 120 (dBuV)")


STATIC_DIR = __import__("os").path.join(
    __import__("os").path.dirname(__file__), "static"
)


def _update_state_file(state_path: Optional[str], **kwargs: object) -> None:
    if not state_path:
        return
    try:
        if os.path.exists(state_path):
            with open(state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        else:
            data = {}
        data.update(kwargs)
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
    except Exception as exc:
        logger.error("Failed to update state file %s: %s", state_path, exc)


def create_app(
    status_bus: StatusBus, cfg_dir: str, state_path: Optional[str] = None
) -> Flask:
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

    @app.get("/")
    def index() -> Response:
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/status")
    def api_status() -> Response:
        return jsonify(status_bus.snapshot())

    @app.get("/api/configs")
    def api_list_configs() -> Response:
        return jsonify(_list_cfgs(cfg_dir))

    @app.get("/api/configs/<name>")
    @app.get("/api/configs-json/<name>")
    def api_get_config_json(name: str) -> Response:
        path = _safe_cfg_path(cfg_dir, name)
        cfg = _load_config_dict(path)
        return jsonify(cfg)

    @app.post("/api/active-config")
    def api_set_active_config() -> Response:
        data = request.get_json(silent=True) or {}
        name = data.get("name")
        if not isinstance(name, str):
            abort(400, "name is required")
        name = cast(str, name)
        path = _safe_cfg_path(cfg_dir, name)
        if not os.path.exists(path):
            abort(404, "config not found")
        status_bus.request_config_switch(path)
        status_bus.set_config_path(path)
        logger.info("Config switch requested: %s", path)
        _update_state_file(state_path, config_path=path)
        return jsonify({"ok": True, "path": path})

    @app.put("/api/configs-json/<name>")
    def api_put_config_json(name: str) -> Response:
        path = _safe_cfg_path(cfg_dir, name)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, "JSON body with config object required")
        _validate_power_range_dict(data)
        _dump_config_dict(path, data)
        return jsonify({"ok": True})

    @app.delete("/api/configs/<name>")
    def api_delete_config(name: str) -> Response:
        path = _safe_cfg_path(cfg_dir, name)
        try:
            os.remove(path)
        except FileNotFoundError:
            abort(404, "config not found")
        return jsonify({"ok": True})

    return app


def run_app(app: Flask, host: str, port: int) -> None:
    app.run(host=host, port=port, threaded=True)


import logging

logger = logging.getLogger(__name__)
