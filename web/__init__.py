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
import logging
import os
import queue
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, NoReturn, Optional, cast

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
            "freq_khz": None,
            "tx_enabled": True,
            "pending_tx": None,
            "pending_reload": False,
        }

    def set_config_path(self, path: str) -> None:
        """Store the active config path."""
        with self._lock:
            self._state["config_path"] = os.path.abspath(path)

    def update_ps(self, ps_list: List[str]) -> None:
        """Update the full PS list."""
        with self._lock:
            self._state["ps"] = list(ps_list)

    def update_ps_current(self, ps_text: str) -> None:
        """Update the current PS text."""
        with self._lock:
            self._state["ps_current"] = ps_text

    def update_rt(self, text: str, bank: int) -> None:
        """Update RT text, bank, and timestamp."""
        with self._lock:
            self._state["rt_text"] = text
            self._state["rt_bank"] = int(bank) & 1
            self._state["rt_updated_at"] = time.time()

    def update_freq(self, khz: float) -> None:
        """Update the current RF frequency (kHz)."""
        with self._lock:
            self._state["freq_khz"] = float(khz)

    def update_tx_enabled(self, enabled: bool) -> None:
        """Update the reported TX enabled state."""
        with self._lock:
            self._state["tx_enabled"] = bool(enabled)

    def request_tx_enabled(self, enabled: bool) -> None:
        """Request a TX on/off toggle."""
        with self._lock:
            self._state["pending_tx"] = bool(enabled)

    def pop_pending_tx(self) -> Optional[bool]:
        """Return and clear the pending TX toggle request."""
        with self._lock:
            val = self._state.get("pending_tx")
            self._state["pending_tx"] = None
            return bool(val) if isinstance(val, bool) else None

    def request_config_switch(self, path: str) -> None:
        """Request a config switch by absolute path."""
        with self._lock:
            self._state["pending_config"] = os.path.abspath(path)

    def current_config_path(self) -> Optional[str]:
        """Return the currently selected config path."""
        with self._lock:
            val = self._state.get("config_path")
            return str(val) if isinstance(val, str) else None

    def pop_pending_config(self) -> Optional[str]:
        """Return and clear the pending config switch request."""
        with self._lock:
            path = self._state.get("pending_config")
            self._state["pending_config"] = None
            return path if isinstance(path, str) else None

    def request_reload(self) -> None:
        """Request a live reload of the active config."""
        with self._lock:
            self._state["pending_reload"] = True

    def pop_pending_reload(self) -> bool:
        """Return and clear the pending reload request."""
        with self._lock:
            pending = bool(self._state.get("pending_reload"))
            self._state["pending_reload"] = False
            return pending

    def snapshot(self) -> Dict[str, object]:
        """Return a serializable snapshot of current status."""
        with self._lock:
            data = dict(self._state)
        # Convert timestamp to ISO-ish string for convenience
        ts = data.get("rt_updated_at")
        if isinstance(ts, (int, float)):
            data["rt_updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))
        return data


class LogBus:
    """Thread-safe ring buffer + pub/sub for log entries."""

    def __init__(self, maxlen: int = 500) -> None:
        self._lock = threading.Lock()
        self._entries: Deque[Dict[str, object]] = deque(maxlen=maxlen)
        self._next_id = 1
        self._subscribers: List[queue.Queue] = []

    def add(self, entry: Dict[str, object]) -> Dict[str, object]:
        """Append an entry and fan out to subscribers."""
        with self._lock:
            entry = dict(entry)
            entry["id"] = self._next_id
            self._next_id += 1
            self._entries.append(entry)
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(entry)
            except queue.Full:
                continue
        return entry

    def snapshot(
        self, limit: int = 200, since_id: Optional[int] = None
    ) -> List[Dict[str, object]]:
        """Return recent entries, optionally after a given id."""
        with self._lock:
            entries = list(self._entries)
        if since_id is not None:
            entries = [
                e
                for e in entries
                if isinstance(e.get("id"), int) and e["id"] > since_id
            ]
        if limit and len(entries) > limit:
            entries = entries[-limit:]
        return entries

    def subscribe(self) -> queue.Queue:
        """Register a subscriber queue for live log entries."""
        q: queue.Queue = queue.Queue(maxsize=200)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        """Unregister a subscriber queue."""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                return


class LogHandler(logging.Handler):
    """Logging handler that forwards records into a LogBus."""

    def __init__(self, log_bus: LogBus) -> None:
        super().__init__()
        self._log_bus = log_bus

    def emit(self, record: logging.LogRecord) -> None:
        try:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(record.created))
            entry = {
                "ts": ts,
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            self._log_bus.add(entry)
        except Exception:
            self.handleError(record)


def attach_log_handler(log_bus: LogBus) -> LogHandler:
    """Attach a LogHandler to the root logger and return it."""
    handler = LogHandler(log_bus)
    logging.getLogger().addHandler(handler)
    return handler


def _safe_cfg_path(cfg_dir: str, name: str) -> str:
    """Validate and resolve a config name to a safe path."""
    if "/" in name or "\\" in name or name.startswith(".") or ".." in name:
        abort(400, "invalid config name")
    if not name.endswith(".json"):
        abort(400, "config must end with .json")
    path = os.path.abspath(os.path.join(cfg_dir, name))
    if not path.startswith(os.path.abspath(cfg_dir) + os.sep):
        abort(400, "invalid config path")
    return path


def _list_cfgs(cfg_dir: str) -> List[str]:
    """List available config JSON files in a directory."""
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
    """Write a file atomically via a temporary file."""
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _load_config_dict(path: str) -> Dict[str, object]:
    """Load a config JSON file into a dict or abort on failure."""
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
    """Serialize config data to JSON with stable formatting."""
    _write_atomic(path, json.dumps(data, indent=2, sort_keys=True))


def _validate_power_range_dict(cfg: Dict[str, object]) -> None:
    """Validate rf.power is within the allowed range, if present."""
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
    """Merge provided fields into the persisted state file."""
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
    status_bus: StatusBus,
    cfg_dir: str,
    state_path: Optional[str] = None,
    log_bus: Optional[LogBus] = None,
) -> Flask:
    """Create the Flask app with API routes and static UI."""
    app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

    @app.get("/")
    def index() -> Response:
        return send_from_directory(STATIC_DIR, "index.html")

    @app.get("/api/status")
    def api_status() -> Response:
        return jsonify(status_bus.snapshot())

    @app.get("/api/tx")
    def api_get_tx() -> Response:
        snap = status_bus.snapshot()
        return jsonify({"enabled": snap.get("tx_enabled")})

    @app.post("/api/tx")
    def api_set_tx() -> Response:
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            abort(400, "enabled must be boolean")
        status_bus.request_tx_enabled(enabled)
        return jsonify({"ok": True, "enabled": enabled})

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

    @app.post("/api/reload-config")
    def api_reload_config() -> Response:
        status_bus.request_reload()
        logger.info("Config reload requested")
        return jsonify({"ok": True})

    @app.put("/api/configs-json/<name>")
    def api_put_config_json(name: str) -> Response:
        path = _safe_cfg_path(cfg_dir, name)
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400, "JSON body with config object required")
        _validate_power_range_dict(data)
        _dump_config_dict(path, data)
        logger.info("Config saved: %s", path)
        return jsonify({"ok": True})

    @app.delete("/api/configs/<name>")
    def api_delete_config(name: str) -> Response:
        path = _safe_cfg_path(cfg_dir, name)
        try:
            os.remove(path)
        except FileNotFoundError:
            abort(404, "config not found")
        return jsonify({"ok": True})

    if log_bus is not None:

        @app.get("/api/logs")
        def api_logs() -> Response:
            limit = request.args.get("limit", type=int) or 200
            since_id = request.args.get("since", type=int)
            return jsonify(log_bus.snapshot(limit=limit, since_id=since_id))

        @app.get("/api/logs/stream")
        def api_logs_stream() -> Response:
            def stream() -> Any:
                q = log_bus.subscribe()
                try:
                    for entry in log_bus.snapshot(limit=200):
                        yield f"data: {json.dumps(entry)}\n\n"
                    while True:
                        try:
                            entry = q.get(timeout=15)
                        except queue.Empty:
                            yield ": keep-alive\n\n"
                            continue
                        yield f"data: {json.dumps(entry)}\n\n"
                finally:
                    log_bus.unsubscribe(q)

            headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
            return Response(stream(), headers=headers, mimetype="text/event-stream")

    return app


def run_app(app: Flask, host: str, port: int) -> None:
    """Run the Flask app (blocking)."""
    app.run(host=host, port=port, threaded=True)
