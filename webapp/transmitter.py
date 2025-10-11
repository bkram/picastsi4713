from __future__ import annotations

import json
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

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
    _ps_pairs,
    _resolve_file_rt,
    _resolve_rotation_rt,
    apply_config as apply_tx_config,
    load_yaml_config as cli_load_yaml_config,
    reconfigure_live,
    recover_tx,
)

LOGGER = logging.getLogger(__name__)


class _HexString(str):
    """String wrapper to emit plain hex scalars in YAML output."""


def _hexstring_representer(dumper: "yaml.Dumper", data: "_HexString"):
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="")


yaml.SafeDumper.add_representer(_HexString, _hexstring_representer)

_MISSING = object()


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
    audio_input_dbfs: Optional[int] = None
    broadcasting: bool = False
    watchdog_active: bool = False
    watchdog_status: str = "idle"
    config_name: Optional[str] = None
    rds: Dict[str, Any] = field(default_factory=dict)
    audio: Dict[str, Any] = field(default_factory=dict)
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
            "audio_input_dbfs": self.audio_input_dbfs,
            "broadcasting": self.broadcasting,
            "watchdog_active": self.watchdog_active,
            "watchdog_status": self.watchdog_status,
            "config_name": self.config_name,
            "rds": self.rds,
            "audio": self.audio,
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


try:  # pragma: no cover - hardware import guard
    from si4713 import SI4713 as HardwareSI4713
except Exception:  # noqa: BLE001
    HardwareSI4713 = None  # type: ignore[assignment]
else:  # pragma: no cover - ensure stub detection
    if not hasattr(HardwareSI4713, "init"):
        HardwareSI4713 = None  # type: ignore[assignment]


class TransmitterManager:
    def __init__(
        self,
        config_root: Path,
        *,
        tx_factory: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.config_root = config_root.resolve()
        self.config_root.mkdir(parents=True, exist_ok=True)

        self._tx = None
        self._tx_factory = tx_factory or self._hardware_factory
        self._config: Optional[AppConfig] = None
        self._config_path: Optional[Path] = None
        self._config_mtime: Optional[float] = None
        self._rt_state: Optional[str] = None
        self._rt_source: str = ""
        self._rotation_idx: int = 0
        self._next_rotation: float = time.monotonic()
        self._rt_file_mtime: Optional[float] = None
        self._ps_slots: List[str] = []
        self._ps_index: int = 0
        self._ps_next_tick: float = time.monotonic()
        self._broadcasting = False
        self._broadcast_since = 0.0
        self._audio_level_dbfs: Optional[int] = None
        self._audio_overmod: bool = False
        self._status_overmod: bool = False
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._metrics = Metrics()
        self._publisher = MetricsPublisher()
        self._watchdog_thread: Optional[threading.Thread] = None
        self._config_applied = False
        self._state_path = self.config_root / ".session-state.json"
        self._session_state = {
            "last_profile": None,
            "broadcast_enabled": False,
        }
        self._load_session_state()

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
        data = self._serialize_config(cfg)
        data["rds"]["pi"] = f"0x{data['rds']['pi']:04X}"
        return data

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
        text = re.sub(
            r"(pi:\s*)'0x([0-9A-Fa-f]+)'",
            lambda match: f"{match.group(1)}0x{match.group(2)}",
            text,
        )
        self.write_config(name, text)

    def _serialize_config(self, cfg: AppConfig) -> Dict[str, Any]:
        return {
            "rf": {
                "frequency_khz": cfg.frequency_khz,
                "power": cfg.power,
                "antenna_cap": cfg.antenna_cap,
            },
            "audio": {
                "stereo": cfg.audio_stereo,
                "agc_on": cfg.audio_agc_on,
                "limiter_on": cfg.audio_limiter_on,
                "comp_thr": cfg.audio_comp_thr,
                "comp_att": cfg.audio_comp_att,
                "comp_rel": cfg.audio_comp_rel,
                "comp_gain": cfg.audio_comp_gain,
                "lim_rel": cfg.audio_lim_rel,
            },
            "rds": {
                "enabled": cfg.rds_enabled,
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
        audio_raw = payload.get("audio", {})
        rds_raw = payload.get("rds", {})
        monitor_raw = payload.get("monitor", {})

        if not isinstance(rf_raw, dict) or not isinstance(rds_raw, dict):
            raise ValidationError("RF and RDS sections are required")
        if audio_raw and not isinstance(audio_raw, dict):
            raise ValidationError("Audio section must be an object")

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

        audio = {
            "stereo": _bool(audio_raw.get("stereo", True))
            if isinstance(audio_raw, dict)
            else True,
            "agc_on": _bool(audio_raw.get("agc_on")) if isinstance(audio_raw, dict) else False,
            "limiter_on": _bool(audio_raw.get("limiter_on", True))
            if isinstance(audio_raw, dict)
            else True,
            "comp_thr": _int(audio_raw.get("comp_thr", -30), "audio.comp_thr"),
            "comp_att": _int(audio_raw.get("comp_att", 0), "audio.comp_att"),
            "comp_rel": _int(audio_raw.get("comp_rel", 2), "audio.comp_rel"),
            "comp_gain": _int(audio_raw.get("comp_gain", 15), "audio.comp_gain"),
            "lim_rel": _int(audio_raw.get("lim_rel", 50), "audio.lim_rel"),
        }

        di_raw = rds_raw.get("di", {}) if isinstance(rds_raw.get("di"), dict) else {}
        ps_list = _list(rds_raw.get("ps"))
        if not ps_list:
            raise ValidationError("At least one PS entry is required")

        rt_raw = rds_raw.get("rt", {}) if isinstance(rds_raw.get("rt"), dict) else {}
        skip_words = _list(rt_raw.get("skip_words", []))

        ab_mode_raw = rt_raw.get("ab_mode", "auto")
        ab_mode = str(ab_mode_raw).strip().lower() if ab_mode_raw is not None else "auto"
        if not ab_mode:
            ab_mode = "auto"
        if ab_mode not in {"legacy", "auto", "bank"}:
            ab_mode = "auto"

        bank: Optional[int] = None
        if ab_mode == "bank":
            bank_val = rt_raw.get("bank")
            if bank_val not in ("", None):
                bank = _int(bank_val, "rds.rt.bank") & 1

        rds = {
            "enabled": _bool(rds_raw.get("enabled", True)),
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
                "ab_mode": ab_mode,
                "repeats": _int(rt_raw.get("repeats", 3), "rds.rt.repeats"),
                "gap_ms": _int(rt_raw.get("gap_ms", 60), "rds.rt.gap_ms"),
            },
        }

        if bank is not None:
            rds["rt"]["bank"] = bank

        monitor = {
            "health": _bool(monitor_raw.get("health", True)),
            "asq": _bool(monitor_raw.get("asq", True)),
            "interval_s": _float(monitor_raw.get("interval_s", 1.0), "monitor.interval_s"),
            "recovery_attempts": _int(monitor_raw.get("recovery_attempts", 3), "monitor.recovery_attempts"),
            "recovery_backoff_s": _float(
                monitor_raw.get("recovery_backoff_s", 0.5), "monitor.recovery_backoff_s"
            ),
        }

        normalized: Dict[str, Any] = {
            "rf": rf,
            "audio": audio,
            "rds": rds,
            "monitor": monitor,
        }

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
            "audio": serialized["audio"],
            "rds": {
                "enabled": serialized["rds"]["enabled"],
                "pi": _HexString(f"0x{serialized['rds']['pi']:04X}"),
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
        LOGGER.info("Applying configuration from %s", path)
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
                LOGGER.debug("Pushing configuration to SI4713")
                self._apply_config_on_backend(tx, cfg)
                LOGGER.debug("Configuration applied successfully")
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Failed to apply configuration to SI4713")
                raise ValidationError(str(exc)) from exc

            self._rt_file_mtime = _get_mtime(cfg.rds_rt_file)
            self._broadcasting = bool(tx.is_transmitting())
            self._broadcast_since = (
                time.monotonic() if self._broadcasting else 0.0
            )
            self._audio_level_dbfs = None
            self._audio_overmod = False
            self._status_overmod = False
            self._metrics.audio_input_dbfs = None
            self._update_metrics(cfg, broadcasting=self._broadcasting)

            if cfg.monitor_health:
                try:
                    transmitting = tx.is_transmitting()
                    LOGGER.debug(
                        "Post-apply health check: transmitting=%s",
                        transmitting,
                    )
                    if transmitting:
                        LOGGER.info(
                            "TX is up at %.2f MHz", cfg.frequency_khz / 1000.0
                        )
                    else:
                        LOGGER.warning("TX not running after setup; attempting recovery")
                        if not recover_tx(tx, cfg):
                            self._broadcasting = False
                            self._broadcast_since = 0.0
                            self._update_metrics(cfg, broadcasting=False)
                            self._metrics.watchdog_status = "failed"
                            self._publisher.broadcast(self._metrics.to_dict())
                            raise ValidationError(
                                "Transmitter failed to start after recovery attempts"
                            )
                        self._broadcasting = True
                        self._broadcast_since = time.monotonic()
                        self._update_metrics(cfg, broadcasting=True)
                        # recover_tx reapplies config; resync internal state
                        LOGGER.info("Recovery succeeded; re-applying configuration")
                        self._apply_config_on_backend(tx, cfg)
                        actual_state = bool(tx.is_transmitting())
                        if not actual_state:
                            LOGGER.debug(
                                "Recover routine did not report TX active; assuming ON"
                            )
                            actual_state = self._broadcasting or True
                        self._broadcasting = actual_state
                        self._broadcast_since = (
                            time.monotonic() if actual_state else 0.0
                        )
                        self._update_metrics(cfg, broadcasting=actual_state)
                except ValidationError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self._broadcasting = False
                    self._broadcast_since = 0.0
                    self._update_metrics(cfg, broadcasting=False)
                    self._metrics.watchdog_status = "failed"
                    self._publisher.broadcast(self._metrics.to_dict())
                    raise ValidationError(str(exc)) from exc

            self._ensure_watchdog()
            relative_name = str(path.relative_to(self.config_root))
            self._update_session_state(
                last_profile=relative_name, broadcast=self._broadcasting
            )
        return self.current_status()

    def set_broadcast(self, enabled: bool) -> Dict[str, Any]:
        with self._lock:
            cfg = self._config
            if enabled:
                if not cfg or self._config_path is None:
                    raise ValidationError("Apply a configuration before enabling broadcast")
                tx = self._ensure_tx()
                if not self._config_applied:
                    try:
                        LOGGER.info("Reapplying configuration before enabling broadcast")
                        self._apply_config_on_backend(tx, cfg)
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.exception("Failed to reapply configuration")
                        raise ValidationError(str(exc)) from exc
            else:
                tx = self._tx
                if tx is None:
                    raise ValidationError("Transmitter not initialised")
            LOGGER.info("Setting broadcast %s", "ON" if enabled else "OFF")
            try:
                tx.enable_mpx(enabled)
                if enabled and cfg:
                    tx.set_stereo_mode(cfg.audio_stereo)
                    tx.set_pilot(freq_hz=19000, dev_hz=675 if cfg.audio_stereo else 0)
                    tx.rds_enable(cfg.rds_enabled)
                actual_state = bool(tx.is_transmitting()) if enabled else False
            except Exception as exc:  # noqa: BLE001
                raise ValidationError(str(exc)) from exc

            self._broadcasting = actual_state
            self._broadcast_since = time.monotonic() if actual_state else 0.0
            if not actual_state:
                self._audio_level_dbfs = None
                self._audio_overmod = False
                self._status_overmod = False
                self._metrics.audio_input_dbfs = None
                if not enabled:
                    self._teardown_tx()
            if cfg:
                if enabled and cfg.monitor_health:
                    try:
                        LOGGER.debug(
                            "Broadcast request complete: requested=%s transmitting=%s",
                            enabled,
                            actual_state,
                        )
                        if not actual_state:
                            LOGGER.warning(
                                "Broadcast state mismatch after request; attempting recovery"
                            )
                            if not recover_tx(tx, cfg):
                                self._broadcasting = False
                                self._broadcast_since = 0.0
                                self._update_metrics(cfg, broadcasting=False)
                                self._metrics.watchdog_status = "failed"
                                self._publisher.broadcast(self._metrics.to_dict())
                                raise ValidationError(
                                    "Transmitter failed to start after recovery attempts"
                                )
                            LOGGER.info("Recovery succeeded; re-applying configuration")
                            self._apply_config_on_backend(tx, cfg)
                            self._broadcasting = bool(tx.is_transmitting())
                            self._broadcast_since = (
                                time.monotonic() if self._broadcasting else 0.0
                            )
                    except ValidationError:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        self._broadcasting = False
                        self._broadcast_since = 0.0
                        self._update_metrics(cfg, broadcasting=False)
                        self._metrics.watchdog_status = "failed"
                        self._publisher.broadcast(self._metrics.to_dict())
                        raise ValidationError(str(exc)) from exc
                if enabled:
                    self._ensure_watchdog()
                self._update_metrics(cfg, self._broadcasting)
            else:
                self._metrics.broadcasting = enabled
                self._metrics.watchdog_status = "running" if enabled else "stopped"
                if not enabled:
                    self._metrics.audio_input_dbfs = None
                    self._metrics.audio = {}
                    self._status_overmod = False
                self._metrics.last_updated = time.time()
                self._publisher.broadcast(self._metrics.to_dict())
        self._update_session_state(broadcast=self._broadcasting)
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
                self._teardown_tx()

    def restore_last_session(self) -> None:
        state = dict(self._session_state)
        profile_name = state.get("last_profile")
        applied = False
        if isinstance(profile_name, str) and profile_name.strip():
            candidate = Path(profile_name.strip())
            try:
                self.apply_config(candidate)
            except FileNotFoundError:
                LOGGER.warning(
                    "Saved profile %s is missing; clearing stored session", profile_name
                )
                self._update_session_state(last_profile=None, broadcast=False)
            except ValidationError as exc:
                LOGGER.warning(
                    "Failed to restore configuration %s: %s", profile_name, exc
                )
            except Exception:  # noqa: BLE001
                LOGGER.exception(
                    "Unexpected error while restoring configuration %s", profile_name
                )
            else:
                applied = True

        if applied:
            try:
                self.set_broadcast(True)
            except ValidationError as exc:
                LOGGER.warning("Failed to resume broadcast automatically: %s", exc)
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error while resuming broadcast")

    def _load_session_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            raw = self._state_path.read_text(encoding="utf-8")
            data = json.loads(raw) if raw.strip() else {}
        except Exception:  # noqa: BLE001
            LOGGER.warning("Unable to read session state from %s", self._state_path)
            return
        if not isinstance(data, dict):
            return
        last_profile = data.get("last_profile")
        if isinstance(last_profile, str) and last_profile.strip():
            self._session_state["last_profile"] = last_profile.strip()
        broadcast = data.get("broadcast_enabled")
        if isinstance(broadcast, bool):
            self._session_state["broadcast_enabled"] = broadcast
        elif broadcast is not None:
            self._session_state["broadcast_enabled"] = bool(broadcast)

    def _persist_session_state(self) -> None:
        payload = {
            "last_profile": self._session_state.get("last_profile"),
            "broadcast_enabled": bool(self._session_state.get("broadcast_enabled")),
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to persist session state to %s", self._state_path)

    def _update_session_state(
        self,
        *,
        last_profile: Any = _MISSING,
        broadcast: Any = _MISSING,
    ) -> None:
        changed = False
        if last_profile is not _MISSING:
            if isinstance(last_profile, Path):
                stored = str(last_profile)
            elif last_profile:
                stored = str(last_profile)
            else:
                stored = None
            self._session_state["last_profile"] = stored
            changed = True
        if broadcast is not _MISSING:
            self._session_state["broadcast_enabled"] = bool(broadcast)
            changed = True
        if changed:
            self._persist_session_state()

    # ---------------------- internal helpers ----------------------

    def _resolve_path(self, name: Path) -> Path:
        path = (self.config_root / name).resolve()
        if not str(path).startswith(str(self.config_root.resolve())):
            raise ValidationError("Path escapes configuration directory")
        return path

    def _hardware_factory(self):
        if HardwareSI4713 is None:
            raise ValidationError("Transmitter hardware unavailable")
        return HardwareSI4713()

    def _ensure_tx(self):
        if self._tx is not None:
            return self._tx

        try:
            tx = self._tx_factory()
        except ValidationError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(f"Failed to construct SI4713: {exc}") from exc

        try:
            initialised = tx.init(RESET_PIN, REFCLK_HZ)
        except Exception as exc:  # noqa: BLE001
            raise ValidationError(str(exc)) from exc

        if not initialised:
            raise ValidationError("SI4713 initialisation failed")

        self._tx = tx
        self._config_applied = False
        return tx

    def _apply_config_on_backend(self, tx: Any, cfg: AppConfig) -> None:
        self._rt_state, self._rt_source, self._rotation_idx, self._next_rotation = (
            apply_tx_config(tx, cfg)
        )
        self._configure_ps_slots(cfg)
        self._config_applied = True

    def _teardown_tx(self) -> None:
        tx = self._tx
        if tx is None:
            return
        try:
            tx.hw_reset(RESET_PIN)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to reset transmitter during teardown")
        try:
            tx.close()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to close transmitter during teardown")
        self._tx = None
        self._config_applied = False

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
            interval = 0.5
            with self._lock:
                cfg = self._config
                tx = self._tx
                broadcast_since = self._broadcast_since
                broadcasting = self._broadcasting

                if not cfg:
                    self._metrics.watchdog_active = False
                    self._metrics.watchdog_status = "idle"
                    self._metrics.audio_input_dbfs = None
                    self._metrics.audio = {}
                    self._status_overmod = False
                    self._metrics.last_updated = time.time()
                    self._publisher.broadcast(self._metrics.to_dict())
                elif tx is None:
                    self._metrics.watchdog_active = False
                    self._metrics.watchdog_status = "stopped"
                    self._metrics.audio_input_dbfs = None
                    self._metrics.audio = {}
                    self._status_overmod = False
                    self._metrics.broadcasting = False
                    self._metrics.last_updated = time.time()
                    self._publisher.broadcast(self._metrics.to_dict())
                else:
                    interval = max(0.1, cfg.health_interval_s)
                    self._metrics.watchdog_active = True
                    self._metrics.watchdog_status = (
                        "running" if broadcasting else "stopped"
                    )

                    overmod_flag = False
                    try:
                        status = tx.tx_status()
                        if status:
                            freq_10khz, power_level, overmod, _ = status
                            self._metrics.frequency_khz = freq_10khz * 10
                            self._metrics.power = power_level
                            overmod_flag = bool(overmod)
                            self._status_overmod = overmod_flag
                            self._metrics.overmodulation = overmod_flag
                            self._metrics.broadcasting = (
                                broadcasting and power_level > 0
                            )
                            self._metrics.last_updated = time.time()
                            LOGGER.debug(
                                "Watchdog metrics: freq=%.2fMHz power=%s overmod=%s broadcasting=%s",
                                self._metrics.frequency_khz / 1000.0,
                                power_level,
                                overmod_flag,
                                broadcasting,
                            )
                        else:
                            self._metrics.broadcasting = False
                            self._status_overmod = False
                    except Exception as exc:  # noqa: BLE001
                        LOGGER.exception("Failed to read transmitter status: %s", exc)
                        self._status_overmod = False

                    try:
                        if cfg.monitor_asq:
                            asq_overmod, input_level = tx.read_asq()
                            self._audio_level_dbfs = input_level
                            self._audio_overmod = asq_overmod
                            self._metrics.audio_input_dbfs = input_level
                            overmod_flag = overmod_flag or asq_overmod
                            LOGGER.debug(
                                "ASQ metrics: level=%sdBFS overmod=%s",
                                input_level,
                                asq_overmod,
                            )
                            if asq_overmod or (input_level is not None and input_level >= 0):
                                LOGGER.warning(
                                    "Audio input high: %sdBFS%s",
                                    input_level,
                                    " (limiter active)" if asq_overmod else "",
                                )
                        else:
                            self._audio_level_dbfs = None
                            self._audio_overmod = False
                            self._metrics.audio_input_dbfs = None
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Failed to read ASQ metrics")

                    self._metrics.audio = self._audio_snapshot(cfg)
                    if broadcasting:
                        self._metrics.overmodulation = bool(
                            overmod_flag or self._audio_overmod
                        )
                    else:
                        self._metrics.overmodulation = False

                    try:
                        self._maybe_reload_config(cfg, tx)
                        cfg = self._config or cfg
                        self._maybe_refresh_rt(cfg, tx)
                        self._maybe_rotate_ps(cfg)
                        if (
                            cfg.monitor_health
                            and broadcasting
                            and self._health_window_open(broadcast_since, cfg)
                            and not tx.is_transmitting()
                        ):
                            LOGGER.warning(
                                "Watchdog detected stopped transmission; attempting recovery"
                            )
                            self._metrics.watchdog_status = "recovering"
                            recovered = recover_tx(tx, cfg)
                            self._metrics.watchdog_status = (
                                "running" if recovered else "failed"
                            )
                            LOGGER.info(
                                "Watchdog recovery %s",
                                "succeeded" if recovered else "failed",
                            )
                    except Exception:  # noqa: BLE001
                        LOGGER.exception("Watchdog loop error")

                    self._publisher.broadcast(self._metrics.to_dict())

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
            self._configure_ps_slots(new_cfg)
            self._metrics.ps = self._current_ps_display()
            self._metrics.frequency_khz = new_cfg.frequency_khz
            self._metrics.power = new_cfg.power
            self._metrics.antenna_cap = new_cfg.antenna_cap
            self._metrics.rds = self._rds_snapshot(new_cfg, self._broadcasting)
            self._metrics.audio = self._audio_snapshot(new_cfg)
            if not new_cfg.rds_enabled:
                self._rt_state = ""
                self._rt_source = "disabled"
                self._metrics.rt = ""
                self._metrics.rt_source = "disabled"
            if rt_changed:
                candidate = _resolve_rotation_rt(new_cfg, self._rotation_idx) or ""
                self._push_rt(tx, new_cfg, candidate, "config")

    def _maybe_refresh_rt(self, cfg: AppConfig, tx: Any) -> None:
        if not cfg.rds_enabled:
            return
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
        if not cfg.rds_enabled:
            return
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
        self._metrics.rds = self._rds_snapshot(cfg, self._broadcasting)
        self._metrics.audio = self._audio_snapshot(cfg)
        self._metrics.last_updated = time.time()
        self._publisher.broadcast(self._metrics.to_dict())

    def _ps_interval(self, cfg: AppConfig) -> float:
        return max(0.5, float(cfg.rds_ps_speed or 1))

    def _configure_ps_slots(self, cfg: AppConfig) -> None:
        pairs = _ps_pairs(cfg.rds_ps, center=cfg.rds_ps_center)
        limit = max(0, cfg.rds_ps_count)
        if limit:
            pairs = pairs[: min(limit, len(pairs))]
        slots = [text for text, _slot in pairs if text.strip()]
        self._ps_slots = slots
        self._ps_index = 0
        if not self._ps_slots:
            self._ps_next_tick = float("inf")
            return
        interval = self._ps_interval(cfg)
        self._ps_next_tick = (
            time.monotonic() + interval if len(self._ps_slots) > 1 else float("inf")
        )

    def _current_ps_display(self) -> str:
        if not self._ps_slots:
            return ""
        index = min(self._ps_index, len(self._ps_slots) - 1)
        if index < 0:
            index = 0
        return self._ps_slots[index].strip()

    def _maybe_rotate_ps(self, cfg: AppConfig) -> None:
        if not cfg.rds_enabled:
            return
        if not self._ps_slots or len(self._ps_slots) == 1:
            return
        now = time.monotonic()
        if now < self._ps_next_tick:
            return
        self._ps_index = (self._ps_index + 1) % len(self._ps_slots)
        self._ps_next_tick = now + self._ps_interval(cfg)
        self._metrics.ps = self._current_ps_display()
        self._metrics.rds = self._rds_snapshot(cfg, self._broadcasting)
        self._metrics.audio = self._audio_snapshot(cfg)
        self._metrics.last_updated = time.time()

    def _health_window_open(self, since: float, cfg: AppConfig) -> bool:
        if since <= 0.0:
            return True
        grace = max(cfg.health_interval_s * 3, 1.0)
        elapsed = time.monotonic() - since
        window_open = elapsed >= grace
        LOGGER.debug(
            "Health window check: since=%.3fs grace=%.3fs open=%s",
            elapsed,
            grace,
            window_open,
        )
        return window_open

    def _update_metrics(self, cfg: AppConfig, broadcasting: bool) -> None:
        self._metrics.ps = self._current_ps_display()
        self._metrics.rt = (self._rt_state or "").strip()
        self._metrics.rt_source = self._rt_source
        self._metrics.frequency_khz = cfg.frequency_khz
        self._metrics.power = cfg.power
        self._metrics.antenna_cap = cfg.antenna_cap
        self._metrics.broadcasting = broadcasting
        self._metrics.watchdog_active = True
        self._metrics.watchdog_status = "running" if broadcasting else "stopped"
        self._metrics.audio_input_dbfs = self._audio_level_dbfs
        self._metrics.audio = self._audio_snapshot(cfg)
        if broadcasting:
            self._metrics.overmodulation = bool(
                self._status_overmod or self._audio_overmod
            )
        else:
            self._metrics.overmodulation = False
        self._metrics.config_name = (
            str(self._config_path.relative_to(self.config_root))
            if self._config_path
            else None
        )
        self._metrics.rds = self._rds_snapshot(cfg, broadcasting)
        self._metrics.last_updated = time.time()
        self._publisher.broadcast(self._metrics.to_dict())

    def _rds_snapshot(self, cfg: AppConfig, broadcasting: bool) -> Dict[str, Any]:
        active_index = self._ps_index if self._ps_slots else None
        current_ps = self._current_ps_display()
        return {
            "enabled": cfg.rds_enabled and broadcasting,
            "configured": cfg.rds_enabled,
            "pi": f"0x{cfg.rds_pi:04X}",
            "pty": cfg.rds_pty,
            "tp": cfg.rds_tp,
            "ta": cfg.rds_ta,
            "ms_music": cfg.rds_ms_music,
            "ps": list(cfg.rds_ps),
            "ps_formatted": list(self._ps_slots),
            "ps_current": current_ps,
            "ps_active_index": active_index,
            "ps_center": cfg.rds_ps_center,
            "ps_count": cfg.rds_ps_count,
            "ps_speed": cfg.rds_ps_speed,
            "di": {
                "stereo": cfg.di_stereo,
                "artificial_head": cfg.di_artificial_head,
                "compressed": cfg.di_compressed,
                "dynamic_pty": cfg.di_dynamic_pty,
            },
        }

    def _audio_snapshot(
        self,
        cfg: AppConfig,
        *,
        input_level: Optional[int] = None,
        overmod: Optional[bool] = None,
    ) -> Dict[str, Any]:
        level = self._audio_level_dbfs if input_level is None else input_level
        overmod_flag = self._audio_overmod if overmod is None else overmod
        return {
            "stereo": cfg.audio_stereo,
            "agc_on": cfg.audio_agc_on,
            "limiter_on": cfg.audio_limiter_on,
            "comp_thr": cfg.audio_comp_thr,
            "comp_att": cfg.audio_comp_att,
            "comp_rel": cfg.audio_comp_rel,
            "comp_gain": cfg.audio_comp_gain,
            "lim_rel": cfg.audio_lim_rel,
            "input_level_dbfs": level,
            "overmod": overmod_flag,
        }
