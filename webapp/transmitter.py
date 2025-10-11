from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import importlib
import sys
import types

import yaml

if "si4713" not in sys.modules:
    try:  # pragma: no cover - prefer actual hardware implementation
        importlib.import_module("si4713")
    except Exception:  # noqa: BLE001
        dummy = types.ModuleType("si4713")
        dummy.SI4713 = object  # type: ignore[attr-defined]
        sys.modules.setdefault("si4713", dummy)

from picast4713 import (
    AppConfig,
    RESET_PIN,
    REFCLK_HZ,
    _burst_rt,
    _get_mtime,
    _resolve_file_rt,
    _resolve_rotation_rt,
    apply_config as apply_tx_config,
    load_yaml_config as cli_load_yaml_config,
    reconfigure_live,
    recover_tx,
)

LOGGER = logging.getLogger(__name__)


class ValidationError(Exception):
    """Raised when a payload or configuration is invalid."""


def _load_app_config_from_yaml(path: Path) -> AppConfig:
    try:
        return cli_load_yaml_config(str(path))
    except SystemExit as exc:  # pragma: no cover - compatibility with CLI path
        raise ValidationError("Invalid configuration") from exc


@dataclass
class Metrics:
    ps: str = ""
    rt: str = ""
    rt_source: str = ""
    frequency_khz: int = 0
    power: int = 0
    antenna_cap: int = 0
    overmodulation: bool = False
    broadcasting: bool = False
    watchdog_active: bool = False
    watchdog_status: str = "idle"
    config_name: Optional[str] = None
    last_updated: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ps": self.ps,
            "rt": self.rt,
            "rt_source": self.rt_source,
            "frequency_khz": self.frequency_khz,
            "power": self.power,
            "antenna_cap": self.antenna_cap,
            "overmodulation": self.overmodulation,
            "broadcasting": self.broadcasting,
            "watchdog_active": self.watchdog_active,
            "watchdog_status": self.watchdog_status,
            "config_name": self.config_name,
            "last_updated": self.last_updated,
        }


class MetricsPublisher:
    def __init__(self) -> None:
        self._queues: List[queue.Queue[Dict[str, Any]]] = []
        self._lock = threading.Lock()

    def register(self) -> queue.Queue[Dict[str, Any]]:
        q: queue.Queue[Dict[str, Any]] = queue.Queue()
        with self._lock:
            self._queues.append(q)
        return q

    def unregister(self, q: queue.Queue[Dict[str, Any]]) -> None:
        with self._lock:
            if q in self._queues:
                self._queues.remove(q)

    def broadcast(self, payload: Dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._queues)
        for q in queues:
            try:
                q.put_nowait(payload)
            except queue.Full:
                continue


class VirtualSI4713:
    """Graceful stub used when hardware is unavailable."""

    def __init__(self) -> None:
        self._transmitting = False
        self._rt = ""
        self._ps: List[str] = [""] * 8
        self._power = 0
        self._antenna = 0
        self._frequency = 0

    def init(self, *_: Any, **__: Any) -> bool:
        LOGGER.warning("Using virtual SI4713 implementation")
        return True

    def set_output(self, power: int, antenna: int) -> None:
        self._power = power
        self._antenna = antenna

    def set_frequency_10khz(self, freq: int) -> None:
        self._frequency = freq * 10

    def enable_mpx(self, on: bool) -> None:
        self._transmitting = on

    def set_pilot(self, *_: Any, **__: Any) -> None:  # pragma: no cover - no-op
        return

    def set_audio(self, *_: Any, **__: Any) -> None:  # pragma: no cover - no-op
        return

    def set_audio_processing(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        return

    def rds_set_pi(self, *_: Any, **__: Any) -> None:  # pragma: no cover - no-op
        return

    def rds_set_pty(self, *_: Any, **__: Any) -> None:  # pragma: no cover - no-op
        return

    def rds_set_tp(self, *_: Any, **__: Any) -> None:  # pragma: no cover - no-op
        return

    def rds_set_ta(self, *_: Any, **__: Any) -> None:  # pragma: no cover - no-op
        return

    def rds_set_ms_music(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        return

    def rds_set_di(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        return

    def rds_set_deviation(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        return

    def rds_set_ps(self, text: str, slot: int) -> None:
        if 0 <= slot < len(self._ps):
            self._ps[slot] = text

    def rds_set_pscount(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        return

    def set_rt_ab_mode(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        return

    def rds_set_rt(self, text: str, *_: Any, **__: Any) -> None:
        self._rt = text

    def rds_enable(self, *_: Any, **__: Any) -> None:  # pragma: no cover
        return

    def hw_reset(self, *_: Any, **__: Any) -> None:
        self._transmitting = False

    def is_transmitting(self) -> bool:
        return self._transmitting

    def tx_status(self) -> Optional[tuple[int, int, bool, int]]:
        power = self._power if self._transmitting else 0
        return (self._frequency // 10, power, False, 0)

    def read_asq(self) -> tuple[bool, int]:
        return False, 0

    def close(self) -> None:  # pragma: no cover - nothing to clean up
        return


try:  # pragma: no cover - hardware import guard
    from si4713 import SI4713 as HardwareSI4713
except Exception:  # noqa: BLE001
    HardwareSI4713 = None  # type: ignore[assignment]
else:  # pragma: no cover - ensure stub detection
    if not hasattr(HardwareSI4713, "init"):
        HardwareSI4713 = None  # type: ignore[assignment]


_TRUTHY = {"1", "true", "yes", "on"}


class TransmitterManager:
    def __init__(self, config_root: Path, *, prefer_virtual: Optional[bool] = None) -> None:
        self.config_root = config_root.resolve()
        self.config_root.mkdir(parents=True, exist_ok=True)

        self._tx = None
        self._tx_backend = "unknown"
        self._config: Optional[AppConfig] = None
        self._config_path: Optional[Path] = None
        self._config_mtime: Optional[float] = None
        self._rt_state: Optional[str] = None
        self._rt_source: str = ""
        self._rotation_idx: int = 0
        self._next_rotation: float = time.monotonic()
        self._rt_file_mtime: Optional[float] = None
        self._broadcasting = False
        self._broadcast_since = 0.0
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._metrics = Metrics()
        self._publisher = MetricsPublisher()
        self._watchdog_thread: Optional[threading.Thread] = None

        if prefer_virtual is None:
            env = os.environ.get("PICAST_USE_VIRTUAL")
            if env is not None:
                prefer_virtual = env.strip().lower() in _TRUTHY
            else:
                prefer_virtual = not Path("/dev/i2c-1").exists()

        self._prefer_virtual = bool(prefer_virtual)
        if self._prefer_virtual:
            LOGGER.info("Virtual SI4713 backend selected (hardware access disabled)")

    # ---------------------- public API ----------------------

    def list_configs(self) -> List[str]:
        configs: List[str] = []
        for path in sorted(self.config_root.rglob("*.yml")):
            configs.append(str(path.relative_to(self.config_root)))
        for path in sorted(self.config_root.rglob("*.yaml")):
            rel = str(path.relative_to(self.config_root))
            if rel not in configs:
                configs.append(rel)
        return configs

    def read_config(self, name: Path) -> str:
        path = self._resolve_path(name)
        if not path.exists():
            raise FileNotFoundError(str(path))
        return path.read_text(encoding="utf-8")

    def load_config(self, name: Path) -> AppConfig:
        raw = self.read_config(name)
        if not raw.strip():
            raise ValidationError("Configuration is empty")
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:  # noqa: BLE001
            raise ValidationError(f"Invalid YAML: {exc}") from exc
        if not isinstance(data, dict):
            raise ValidationError("Configuration must be a mapping")
        try:
            return AppConfig(data)
        except SystemExit as exc:  # pragma: no cover - reuse CLI validation
            raise ValidationError("Invalid configuration") from exc

    def read_config_struct(self, name: Path) -> Dict[str, Any]:
        cfg = self.load_config(name)
        return self._serialize_config(cfg)

    def write_config(self, name: Path, payload: str) -> None:
        try:
            data = yaml.safe_load(payload) if payload.strip() else {}
        except yaml.YAMLError as exc:  # noqa: BLE001
            raise ValidationError(f"Invalid YAML: {exc}") from exc
        if data:
            try:
                AppConfig(data)
            except SystemExit as exc:  # pragma: no cover - reuse CLI validation
                raise ValidationError("Invalid configuration") from exc
        path = self._resolve_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")

    def write_config_struct(self, name: Path, payload: Dict[str, Any]) -> None:
        normalized = self._normalize_struct(payload)
        text = yaml.safe_dump(normalized, sort_keys=False)
        self.write_config(name, text)

    def _serialize_config(self, cfg: AppConfig) -> Dict[str, Any]:
        return {
            "rf": {
                "frequency_khz": cfg.frequency_khz,
                "power": cfg.power,
                "antenna_cap": cfg.antenna_cap,
            },
            "rds": {
                "pi": cfg.rds_pi,
                "pty": cfg.rds_pty,
                "tp": cfg.rds_tp,
                "ta": cfg.rds_ta,
                "ms_music": cfg.rds_ms_music,
                "di": {
                    "stereo": cfg.di_stereo,
                    "artificial_head": cfg.di_artificial_head,
                    "compressed": cfg.di_compressed,
                    "dynamic_pty": cfg.di_dynamic_pty,
                },
                "ps": cfg.rds_ps,
                "ps_center": cfg.rds_ps_center,
                "ps_speed": cfg.rds_ps_speed,
                "ps_count": cfg.rds_ps_count,
                "deviation_hz": cfg.rds_dev_hz,
                "rt": {
                    "text": cfg.rds_rt_text,
                    "texts": cfg.rds_rt_texts,
                    "speed_s": cfg.rds_rt_speed_s,
                    "center": cfg.rds_rt_center,
                    "file_path": cfg.rds_rt_file or "",
                    "skip_words": cfg.rds_rt_skip_words,
                    "ab_mode": cfg.rds_rt_ab_mode,
                    "repeats": cfg.rds_rt_repeats,
                    "gap_ms": cfg.rds_rt_gap_ms,
                    "bank": cfg.rds_rt_bank,
                },
            },
            "monitor": {
                "health": cfg.monitor_health,
                "asq": cfg.monitor_asq,
                "interval_s": cfg.health_interval_s,
                "recovery_attempts": cfg.recovery_attempts,
                "recovery_backoff_s": cfg.recovery_backoff_s,
            },
        }

    def _normalize_struct(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            raise ValidationError("Configuration payload must be a mapping")

        rf_raw = payload.get("rf", {})
        rds_raw = payload.get("rds", {})
        monitor_raw = payload.get("monitor", {})

        if not isinstance(rf_raw, dict) or not isinstance(rds_raw, dict):
            raise ValidationError("RF and RDS sections are required")

        def _int(value: Any, field: str) -> int:
            try:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return int(value)
                if isinstance(value, str):
                    cleaned = value.strip()
                    return int(cleaned or 0, 0)
                return int(value)
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(f"{field} must be an integer") from exc

        def _float(value: Any, field: str) -> float:
            try:
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return float(value)
                if isinstance(value, str):
                    cleaned = value.strip()
                    return float(cleaned or 0)
                return float(value)
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(f"{field} must be a number") from exc

        def _bool(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return bool(value)
            if isinstance(value, str):
                v = value.strip().lower()
                if v in {"1", "true", "yes", "on"}:
                    return True
                if v in {"0", "false", "no", "off"}:
                    return False
            return False

        def _list(value: Any) -> List[str]:
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            if isinstance(value, str):
                parts = [part.strip() for part in value.splitlines()]
                return [part for part in parts if part]
            return []

        rf = {
            "frequency_khz": _int(rf_raw.get("frequency_khz"), "rf.frequency_khz"),
            "power": _int(rf_raw.get("power"), "rf.power"),
            "antenna_cap": _int(rf_raw.get("antenna_cap", 4), "rf.antenna_cap"),
        }

        di_raw = rds_raw.get("di", {}) if isinstance(rds_raw.get("di"), dict) else {}
        ps_list = _list(rds_raw.get("ps"))
        if not ps_list:
            raise ValidationError("At least one PS entry is required")

        rt_raw = rds_raw.get("rt", {}) if isinstance(rds_raw.get("rt"), dict) else {}
        skip_words = _list(rt_raw.get("skip_words", []))

        bank_val = rt_raw.get("bank")
        bank: Optional[int]
        if bank_val in ("", None):
            bank = None
        else:
            bank = _int(bank_val) & 1

        rds = {
            "pi": _int(rds_raw.get("pi"), "rds.pi"),
            "pty": _int(rds_raw.get("pty"), "rds.pty"),
            "tp": _bool(rds_raw.get("tp", True)),
            "ta": _bool(rds_raw.get("ta", False)),
            "ms_music": _bool(rds_raw.get("ms_music", True)),
            "ps": ps_list,
            "ps_center": _bool(rds_raw.get("ps_center", True)),
            "ps_speed": _int(rds_raw.get("ps_speed", 10), "rds.ps_speed"),
            "ps_count": _int(rds_raw.get("ps_count", len(ps_list)), "rds.ps_count"),
            "deviation_hz": _int(rds_raw.get("deviation_hz", 200), "rds.deviation_hz"),
            "di": {
                "stereo": _bool(di_raw.get("stereo", True)),
                "artificial_head": _bool(di_raw.get("artificial_head", False)),
                "compressed": _bool(di_raw.get("compressed", False)),
                "dynamic_pty": _bool(di_raw.get("dynamic_pty", False)),
            },
            "rt": {
                "text": str(rt_raw.get("text", "")),
                "texts": _list(rt_raw.get("texts", [])),
                "speed_s": _float(rt_raw.get("speed_s", 10.0), "rds.rt.speed_s"),
                "center": _bool(rt_raw.get("center", True)),
                "file_path": str(rt_raw.get("file_path")) if rt_raw.get("file_path") else None,
                "skip_words": skip_words,
                "ab_mode": str(rt_raw.get("ab_mode", "auto")) or "auto",
                "repeats": _int(rt_raw.get("repeats", 3), "rds.rt.repeats"),
                "gap_ms": _int(rt_raw.get("gap_ms", 60), "rds.rt.gap_ms"),
                "bank": bank,
            },
        }

        monitor = {
            "health": _bool(monitor_raw.get("health", True)),
            "asq": _bool(monitor_raw.get("asq", True)),
            "interval_s": _float(monitor_raw.get("interval_s", 1.0), "monitor.interval_s"),
            "recovery_attempts": _int(monitor_raw.get("recovery_attempts", 3), "monitor.recovery_attempts"),
            "recovery_backoff_s": _float(
                monitor_raw.get("recovery_backoff_s", 0.5), "monitor.recovery_backoff_s"
            ),
        }

        normalized: Dict[str, Any] = {"rf": rf, "rds": rds, "monitor": monitor}

        cfg = AppConfig(normalized)
        # Ensure AppConfig normalisation (e.g. bool coercion) is reflected in saved data
        serialized = self._serialize_config(cfg)

        # Convert serialized structure back into CLI-friendly mapping
        rt_section = serialized["rds"]["rt"].copy()
        if not rt_section.get("file_path"):
            rt_section.pop("file_path", None)
        else:
            rt_section["file_path"] = rt_section["file_path"]

        mapping: Dict[str, Any] = {
            "rf": serialized["rf"],
            "rds": {
                "pi": serialized["rds"]["pi"],
                "pty": serialized["rds"]["pty"],
                "tp": serialized["rds"]["tp"],
                "ta": serialized["rds"]["ta"],
                "ms_music": serialized["rds"]["ms_music"],
                "ps": serialized["rds"]["ps"],
                "ps_center": serialized["rds"]["ps_center"],
                "ps_speed": serialized["rds"]["ps_speed"],
                "ps_count": serialized["rds"]["ps_count"],
                "deviation_hz": serialized["rds"]["deviation_hz"],
                "di": serialized["rds"]["di"],
                "rt": rt_section,
            },
            "monitor": serialized["monitor"],
        }

        if mapping["rds"]["rt"].get("bank") is None:
            mapping["rds"]["rt"].pop("bank", None)

        return mapping

    def apply_config(self, name: Path) -> Dict[str, Any]:
        path = self._resolve_path(name)
        if not path.exists():
            raise FileNotFoundError(str(path))

        cfg = _load_app_config_from_yaml(path)
        with self._lock:
            tx = self._ensure_tx()
            if not tx:
                raise ValidationError("Transmitter hardware unavailable")

            self._config = cfg
            self._config_path = path
            self._config_mtime = _get_mtime(str(path))
            try:
                self._apply_config_on_backend(tx, cfg)
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Failed to apply configuration using %s backend", self._tx_backend)
                fallback = None
                if self._tx_backend != "virtual":
                    fallback = self._switch_to_virtual_backend(str(exc))
                if fallback is None:
                    raise ValidationError(str(exc)) from exc
                tx = fallback
                try:
                    self._apply_config_on_backend(tx, cfg)
                except Exception as retry_exc:  # noqa: BLE001
                    LOGGER.exception("Failed to apply configuration on virtual backend")
                    raise ValidationError(str(retry_exc)) from retry_exc
            self._rt_file_mtime = _get_mtime(cfg.rds_rt_file)
            self._broadcasting = True
            self._broadcast_since = time.monotonic()
            self._update_metrics(cfg, broadcasting=True)
            self._ensure_watchdog()
        return self.current_status()

    def set_broadcast(self, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            cfg = self._config
            if enabled:
                if not cfg or self._config_path is None:
                    raise ValidationError("Apply a configuration before enabling broadcast")
                tx = self._ensure_tx()
            else:
                tx = self._tx
                if tx is None:
                    raise ValidationError("Transmitter not initialised")
            try:
                tx.enable_mpx(enabled)
            except Exception as exc:  # noqa: BLE001
                fallback = None
                if enabled and self._tx_backend != "virtual":
                    fallback = self._switch_to_virtual_backend(str(exc))
                if fallback is None:
                    raise ValidationError(str(exc)) from exc
                tx = fallback
                try:
                    if enabled and cfg:
                        self._apply_config_on_backend(tx, cfg)
                        self._rt_file_mtime = _get_mtime(cfg.rds_rt_file)
                    else:
                        tx.enable_mpx(enabled)
                except Exception as retry_exc:  # noqa: BLE001
                    raise ValidationError(str(retry_exc)) from retry_exc
            self._broadcasting = enabled
            self._broadcast_since = time.monotonic() if enabled else 0.0
            if cfg:
                if enabled:
                    self._ensure_watchdog()
                self._update_metrics(cfg, enabled)
            else:
                self._metrics.broadcasting = enabled
                self._metrics.watchdog_status = "running" if enabled else "paused"
                self._metrics.last_updated = time.time()
                self._publisher.broadcast(self._metrics.to_dict())
        return self.current_status()

    def current_status(self) -> Dict[str, Any]:
        with self._lock:
            return self._metrics.to_dict()

    def metrics_queue(self) -> queue.Queue[Dict[str, Any]]:
        return self._publisher.register()

    def unregister_queue(self, q: queue.Queue[Dict[str, Any]]) -> None:
        self._publisher.unregister(q)

    def shutdown(self) -> None:
        self._stop_event.set()
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=1)
        with self._lock:
            if self._tx is not None:
                try:
                    self._tx.hw_reset(RESET_PIN)
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Failed to reset transmitter during shutdown")
                finally:
                    try:
                        self._tx.close()
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Failed to close transmitter")
                self._tx = None

    # ---------------------- internal helpers ----------------------

    def _resolve_path(self, name: Path) -> Path:
        path = (self.config_root / name).resolve()
        if not str(path).startswith(str(self.config_root.resolve())):
            raise ValidationError("Path escapes configuration directory")
        return path

    def _ensure_tx(self):
        if self._tx is not None:
            return self._tx

        candidates = []
        if not self._prefer_virtual and HardwareSI4713 is not None:
            candidates.append(("hardware", HardwareSI4713))
        candidates.append(("virtual", VirtualSI4713))

        last_error: Optional[Exception] = None
        for backend, impl in candidates:
            try:
                tx = impl()  # type: ignore[operator]
                if not tx.init(RESET_PIN, REFCLK_HZ):
                    raise RuntimeError("initialisation failed")
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception(
                    "Failed to initialise %s SI4713 backend: %s", backend, exc
                )
                last_error = exc
                continue

            self._tx = tx
            self._tx_backend = backend
            return tx

        raise ValidationError("Transmitter hardware unavailable") from last_error

    def _switch_to_virtual_backend(self, reason: str) -> Optional[Any]:
        if isinstance(self._tx, VirtualSI4713):
            return self._tx

        LOGGER.warning("Switching to virtual SI4713 backend (%s)", reason)

        tx = self._tx
        if tx is not None:
            try:
                tx.close()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Failed to close hardware transmitter")

        virtual = VirtualSI4713()
        try:
            virtual.init(RESET_PIN, REFCLK_HZ)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Virtual SI4713 initialisation failed: %s", exc)
            return None

        self._tx = virtual
        self._tx_backend = "virtual"
        return virtual

    def _apply_config_on_backend(self, tx: Any, cfg: AppConfig) -> None:
        self._rt_state, self._rt_source, self._rotation_idx, self._next_rotation = (
            apply_tx_config(tx, cfg)
        )

    def _ensure_watchdog(self) -> None:
        if self._watchdog_thread and self._watchdog_thread.is_alive():
            return
        self._stop_event.clear()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop, name="si4713-watchdog", daemon=True
        )
        self._watchdog_thread.start()

    def _watchdog_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                cfg = self._config
                tx = self._tx
                broadcast_since = self._broadcast_since
                broadcasting = self._broadcasting
            if not cfg or tx is None:
                time.sleep(0.5)
                continue

            interval = max(0.1, cfg.health_interval_s)
            self._metrics.watchdog_active = True
            self._metrics.watchdog_status = "running" if broadcasting else "paused"

            try:
                status = tx.tx_status()
                if status:
                    freq_10khz, power_level, overmod, _ = status
                    self._metrics.frequency_khz = freq_10khz * 10
                    self._metrics.power = power_level
                    self._metrics.overmodulation = overmod
                    self._metrics.broadcasting = broadcasting and power_level > 0
                    self._metrics.last_updated = time.time()
                else:
                    self._metrics.broadcasting = False
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Failed to read transmitter status: %s", exc)

            self._publisher.broadcast(self._metrics.to_dict())

            try:
                self._maybe_reload_config(cfg, tx)
                self._maybe_refresh_rt(cfg, tx)
                if (
                    cfg.monitor_health
                    and broadcasting
                    and self._health_window_open(broadcast_since, cfg)
                    and not tx.is_transmitting()
                ):
                    self._metrics.watchdog_status = "recovering"
                    recovered = recover_tx(tx, cfg)
                    self._metrics.watchdog_status = "running" if recovered else "failed"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Watchdog loop error")

            time.sleep(interval)

    def _maybe_reload_config(self, cfg: AppConfig, tx: Any) -> None:
        if not self._config_path:
            return
        mtime = _get_mtime(str(self._config_path))
        if mtime and mtime != self._config_mtime:
            LOGGER.info("Config file changed on disk; reloading")
            new_cfg = _load_app_config_from_yaml(self._config_path)
            rt_changed = reconfigure_live(tx, cfg, new_cfg)
            self._config = new_cfg
            self._config_mtime = mtime
            self._metrics.frequency_khz = new_cfg.frequency_khz
            self._metrics.power = new_cfg.power
            self._metrics.antenna_cap = new_cfg.antenna_cap
            if rt_changed:
                candidate = _resolve_rotation_rt(new_cfg, self._rotation_idx) or ""
                self._push_rt(tx, new_cfg, candidate, "config")

    def _maybe_refresh_rt(self, cfg: AppConfig, tx: Any) -> None:
        now = time.monotonic()
        if cfg.rds_rt_file:
            current_mtime = _get_mtime(cfg.rds_rt_file)
            if current_mtime and current_mtime != self._rt_file_mtime:
                candidate = _resolve_file_rt(cfg)
                if candidate is not None:
                    self._push_rt(tx, cfg, candidate, "file")
                    self._rt_file_mtime = current_mtime
                    return
                self._rt_file_mtime = current_mtime
        if (
            self._rt_source != "file"
            and cfg.rds_rt_texts
            and now >= self._next_rotation
        ):
            self._rotation_idx = (self._rotation_idx + 1) % len(cfg.rds_rt_texts)
            candidate = _resolve_rotation_rt(cfg, self._rotation_idx) or ""
            self._push_rt(tx, cfg, candidate, f"list[{self._rotation_idx}]")
            self._next_rotation = now + max(0.5, cfg.rds_rt_speed_s)

    def _push_rt(self, tx: Any, cfg: AppConfig, candidate: str, source: str) -> None:
        try:
            _burst_rt(
                tx,
                candidate,
                ab_mode=cfg.rds_rt_ab_mode,
                repeats=cfg.rds_rt_repeats,
                gap_ms=cfg.rds_rt_gap_ms,
                bank=cfg.rds_rt_bank,
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Failed to send RT: %s", exc)
            return
        self._rt_state = candidate
        self._rt_source = source
        self._metrics.rt = candidate.strip()
        self._metrics.rt_source = source
        self._metrics.ps = cfg.rds_ps[0] if cfg.rds_ps else ""
        self._metrics.last_updated = time.time()
        self._publisher.broadcast(self._metrics.to_dict())

    def _health_window_open(self, since: float, cfg: AppConfig) -> bool:
        if since <= 0.0:
            return True
        grace = max(cfg.health_interval_s * 3, 1.0)
        return time.monotonic() - since >= grace

    def _update_metrics(self, cfg: AppConfig, broadcasting: bool) -> None:
        self._metrics.ps = cfg.rds_ps[0] if cfg.rds_ps else ""
        self._metrics.rt = (self._rt_state or "").strip()
        self._metrics.rt_source = self._rt_source
        self._metrics.frequency_khz = cfg.frequency_khz
        self._metrics.power = cfg.power
        self._metrics.antenna_cap = cfg.antenna_cap
        self._metrics.broadcasting = broadcasting
        self._metrics.watchdog_active = True
        self._metrics.watchdog_status = "running" if broadcasting else "paused"
        self._metrics.config_name = (
            str(self._config_path.relative_to(self.config_root))
            if self._config_path
            else None
        )
        self._metrics.last_updated = time.time()
        self._publisher.broadcast(self._metrics.to_dict())
