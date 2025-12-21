#!/usr/bin/env python3
"""
SI4713 FM+RDS transmitter

Usage:
    python3 picast4713.py --cfg station.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import signal
import shlex
import subprocess
import sys
import threading
import time
import socket
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from si4713 import SI4713

if TYPE_CHECKING:
    from web import LogBus, StatusBus

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------


def _resolve_log_level(value: Optional[str]) -> int:
    """Resolve log level from string or numeric value."""
    if not value:
        return logging.INFO
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    return getattr(logging, raw.upper(), logging.INFO)


LOG_LEVEL = _resolve_log_level(os.getenv("LOG_LEVEL", "INFO"))

logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("tx")

# ---------------------------------------------------------------------
# Hardware constants
# ---------------------------------------------------------------------

RESET_PIN: int = int(os.getenv("SI4713_RESET_PIN", "5"))
REFCLK_HZ: int = int(os.getenv("SI4713_REFCLK_HZ", "32768"))
STATE_PATH: str = os.getenv(
    "SI4713_STATE_PATH",
    os.path.join(os.path.dirname(__file__), "cfg", "state.json"),
)
DEFAULT_CFG_PATH: str = os.path.join(os.path.dirname(__file__), "cfg", "default.json")
ADAPTER_CFG_PATH: str = os.path.join(os.path.dirname(__file__), "cfg", "config.yaml")

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------


def _parse_int(value: Any, default: int) -> int:
    """Parse an int from a value or return a default on failure."""
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return default


def _parse_bool(value: Any, default: bool) -> bool:
    """Parse a bool from bool/str inputs; fall back to default."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    return default


def _parse_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Parse a float or return a default when conversion fails."""
    try:
        return float(value)
    except Exception:
        return default


def _parse_antenna_cap(value: Any, default: int = 4) -> Tuple[int, bool]:
    """Return (cap_value, is_auto). Value of 0 or 'auto'/None => auto-tune."""
    if value is None:
        return 0, True
    if isinstance(value, str) and value.strip().lower() == "auto":
        return 0, True

    cap = max(0, min(255, _parse_int(value, default)))
    return cap, cap == 0


def _parse_str(value: Any, default: str = "") -> str:
    """Coerce simple scalar values to string, otherwise return default."""
    return str(value) if isinstance(value, (str, int, float)) else default


def _list_of_str(v: Any) -> List[str]:
    """Return a list of stringified items or an empty list."""
    if not isinstance(v, list):
        return []
    return [str(x) for x in v]


def _get_mtime(path: Optional[str]) -> Optional[float]:
    """Get mtime for path, returning None for missing or invalid paths."""
    if not path:
        return None
    try:
        return os.path.getmtime(path)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _read_text_file(path: str, max_bytes: int = 8192) -> Optional[str]:
    """Read a text file with newline normalization and a size cap."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read(max_bytes)
        return data.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    except Exception as exc:  # noqa: BLE001
        logger.error("RT file read failed (%s): %s", path, exc)
        return None


def _enforce(cond: bool, msg: str) -> None:
    """Abort execution with a config error when a condition fails."""
    if not cond:
        logger.critical("Config error: %s", msg)
        raise SystemExit(2)


def _center_fixed(s: str, width: int) -> str:
    """Center a string within a fixed-width field."""
    if len(s) >= width:
        return s[:width]
    pad = width - len(s)
    left = (pad + 1) // 2
    right = pad - left
    return (" " * left) + s + (" " * right)


def _normalize_rt_source(raw: str) -> str:
    """Normalize RT content to a single line with collapsed whitespace."""
    line = next((ln for ln in raw.split("\n") if ln.strip()), "")
    return " ".join(line.split())


_MACRO_PATTERN = re.compile(
    r"{(time|date|datetime|config|freq|power)}", re.IGNORECASE
)


def _macro_context(
    config_name: str,
    now: Optional[float] = None,
    freq_khz: Optional[int] = None,
    power: Optional[int] = None,
) -> Dict[str, str]:
    """Build macro context values for PS/RT substitution."""
    ts = time.localtime(now or time.time())
    base = os.path.splitext(os.path.basename(config_name or ""))[0]
    freq_val = ""
    if isinstance(freq_khz, (int, float)) and freq_khz > 0:
        freq_val = f"{float(freq_khz) / 1000:.1f}"
    power_val = ""
    if isinstance(power, (int, float)) and power > 0:
        power_val = f"{int(power)}"
    return {
        "time": time.strftime("%H:%M", ts),
        "date": time.strftime("%Y-%m-%d", ts),
        "datetime": time.strftime("%Y-%m-%d %H:%M:%S", ts),
        "config": base or config_name or "",
        "freq": freq_val,
        "power": power_val,
    }


def _apply_macros(text: str, ctx: Dict[str, str]) -> str:
    """Apply macro replacements in a text string."""
    if not text:
        return ""

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        return ctx.get(key, match.group(0))

    return _MACRO_PATTERN.sub(_repl, text)


def _has_macros(text: str) -> bool:
    """Return True if the text contains macro placeholders."""
    return bool(text and _MACRO_PATTERN.search(text))


def _render_ps_slots(
    ps: List[str], center: bool, macro_ctx: Dict[str, str]
) -> Tuple[List[Tuple[str, int]], List[str]]:
    """Render PS slots and their indices with macros and centering."""
    slots: List[Tuple[str, int]] = []
    rendered: List[str] = []
    for idx, item in enumerate(ps):
        txt = _apply_macros(item or "", macro_ctx)
        text8 = _center_fixed(txt, 8) if center else txt[:8].ljust(8)
        slots.append((text8, idx))
        rendered.append(text8)
    return slots, rendered


def _rt_macros_possible(cfg: "AppConfig") -> bool:
    """Return True if RT sources might contain macros."""
    if _has_macros(cfg.rds_rt_text):
        return True
    if any(_has_macros(text) for text in cfg.rds_rt_texts):
        return True
    return bool(cfg.rds_rt_file)


_EMPTY_MACRO_CTX: Dict[str, str] = {}


@dataclass
class MacroContextCache:
    """Cache macro context values, refreshing at most once per second."""

    config_name: str
    freq_khz: Optional[int] = None
    power: Optional[int] = None
    _last_epoch: int = 0
    _last_ctx: Dict[str, str] = field(default_factory=dict)

    def set_config(self, name: str, freq_khz: Optional[int], power: Optional[int]) -> None:
        """Update config name/frequency and reset cached values."""
        self.config_name = name
        self.freq_khz = freq_khz
        self.power = power
        self._last_epoch = 0
        self._last_ctx = {}

    def set_config_name(self, name: str) -> None:
        """Update config name and reset cached values."""
        self.set_config(name, self.freq_khz, self.power)

    def get(self) -> Dict[str, str]:
        """Return cached macro context, refreshing once per second."""
        epoch = int(time.time())
        if epoch != self._last_epoch:
            self._last_ctx = _macro_context(
                self.config_name, epoch, self.freq_khz, self.power
            )
            self._last_epoch = epoch
        return self._last_ctx


# ---------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------


class AppConfig:
    """Parsed, validated application configuration."""
    # RF
    frequency_khz: int
    power: int  # 88..120 dBµV
    antenna_cap: int
    antenna_cap_auto: bool
    audio_dev_hz: int
    audio_dev_no_rds_hz: Optional[int]
    preemph_us: int
    audio_play_enabled: bool
    audio_stream_url: str
    manual_deviation: bool
    audio_device: Optional[str]

    # RDS flags
    rds_pi: int
    rds_pty: int
    rds_tp: bool
    rds_ta: bool
    rds_ms_music: bool
    rds_enabled: bool
    di_stereo: bool
    di_artificial_head: bool
    di_compressed: bool
    di_dynamic_pty: bool

    # PS
    rds_ps: List[str]
    rds_ps_center: bool
    rds_ps_speed: float

    # RT
    rds_dev_hz: int  # 10 Hz units (e.g., 200 => 2.00 kHz)
    rds_rt_text: str
    rds_rt_texts: List[str]
    rds_rt_speed_s: float
    rds_rt_center: bool
    rds_rt_file: Optional[str]
    rds_rt_skip_words: List[str]
    rds_rt_ab_mode: str  # 'legacy' | 'auto' | 'bank'
    rds_rt_repeats: int
    rds_rt_gap_ms: int
    rds_rt_bank: Optional[int]  # used only when ab_mode='bank'

    # Monitor
    monitor_health: bool
    monitor_asq: bool
    health_interval_s: float
    recovery_attempts: int
    recovery_backoff_s: float
    monitor_overmod_ignore_dbfs: Optional[float]

    # UECP input
    uecp_enabled: bool
    uecp_host: str
    uecp_port: int

    def __init__(self, raw: Dict[str, Any]) -> None:
        _enforce(isinstance(raw, dict), "root must be a mapping")

        rf = raw.get("rf", {})
        rds = raw.get("rds", {})
        monitor = raw.get("monitor", {})
        uecp = raw.get("uecp", {})

        _enforce(isinstance(rf, dict), "rf must be a mapping")
        _enforce(isinstance(rds, dict), "rds must be a mapping")
        _enforce(isinstance(uecp, dict), "uecp must be a mapping")

        # RF
        _enforce("frequency_khz" in rf, "rf.frequency_khz is required")
        _enforce("power" in rf, "rf.power is required")
        self.frequency_khz = _parse_int(rf.get("frequency_khz"), 0)
        _enforce(self.frequency_khz > 0, "rf.frequency_khz must be > 0")

        pwr = _parse_int(rf.get("power"), -1)
        _enforce(88 <= pwr <= 120, "rf.power must be in 88..120 dBµV")
        if pwr > 115:
            logger.warning(
                "rf.power %d dBµV above datasheet (115); using high power.", pwr
            )
        self.power = pwr
        cap_val, cap_auto = _parse_antenna_cap(rf.get("antenna_cap", 4), 4)
        cap_auto = cap_auto or _parse_bool(rf.get("antenna_cap_auto", False), False)
        self.antenna_cap_auto = cap_auto
        self.antenna_cap = 0 if cap_auto else cap_val
        self.manual_deviation = _parse_bool(rf.get("manual_deviation", True), True)
        base_dev = _parse_int(rf.get("audio_deviation_hz", 7500), 7500)
        raw_audio_no_rds = rf.get("audio_deviation_no_rds_hz", None)
        self.audio_dev_hz = max(0, base_dev) if self.manual_deviation else 7500
        self.audio_dev_no_rds_hz = (
            max(0, _parse_int(raw_audio_no_rds, self.audio_dev_hz))
            if self.manual_deviation and raw_audio_no_rds is not None
            else None
        )
        pre = str(rf.get("preemphasis", "us50")).lower()
        if pre in {"us75", "75", "us"}:
            self.preemph_us = 75
        elif pre in {"none", "0", "off"}:
            self.preemph_us = 0
        else:
            self.preemph_us = 50
        streaming_cfg = (
            raw.get("streaming", {}) if isinstance(raw.get("streaming"), dict) else {}
        )
        legacy_audio = rf.get("audio", {}) if isinstance(rf.get("audio"), dict) else {}
        audio_cfg = streaming_cfg or legacy_audio
        self.audio_play_enabled = _parse_bool(audio_cfg.get("enabled", False), False)
        self.audio_stream_url = _parse_str(
            audio_cfg.get("url", audio_cfg.get("stream_url", "")), ""
        )

        # RDS flags
        _enforce("pi" in rds, "rds.pi is required")
        _enforce("pty" in rds, "rds.pty is required")
        _enforce("ps" in rds, "rds.ps is required")
        _enforce(
            isinstance(rds.get("ps"), list) and rds["ps"],
            "rds.ps must be a non-empty list",
        )

        self.rds_enabled = _parse_bool(rds.get("enabled", True), True)
        self.rds_pi = _parse_int(rds.get("pi"), 0)
        self.rds_pty = max(0, min(31, _parse_int(rds.get("pty"), 0)))
        self.rds_tp = _parse_bool(rds.get("tp", True), True)
        self.rds_ta = _parse_bool(rds.get("ta", False), False)
        self.rds_ms_music = _parse_bool(rds.get("ms_music", True), True)

        di = rds.get("di", {}) if isinstance(rds.get("di"), dict) else {}
        self.di_stereo = _parse_bool(di.get("stereo", True), True)
        self.di_artificial_head = _parse_bool(di.get("artificial_head", False), False)
        self.di_compressed = _parse_bool(di.get("compressed", False), False)
        self.di_dynamic_pty = _parse_bool(di.get("dynamic_pty", False), False)

        # PS
        self.rds_ps = _list_of_str(rds.get("ps"))
        self.rds_ps_center = _parse_bool(rds.get("ps_center", True), True)
        self.rds_ps_speed = max(
            0.1, float(_parse_float(rds.get("ps_speed", 10.0), 10.0) or 10.0)
        )
        self.rds_ps_count = max(1, len(self.rds_ps))

        # RT core
        self.rds_dev_hz = _parse_int(rds.get("deviation_hz", 200), 200)  # 10 Hz units
        rt_cfg = rds.get("rt", {}) if isinstance(rds.get("rt"), dict) else {}
        self.rds_rt_text = _parse_str(rt_cfg.get("text", ""), "")
        self.rds_rt_texts = [
            t for t in _list_of_str(rt_cfg.get("texts", [])) if t.strip()
        ]
        self.rds_rt_speed_s = float(rt_cfg.get("speed_s", 10.0))
        self.rds_rt_center = _parse_bool(rt_cfg.get("center", True), True)
        file_path = _parse_str(rt_cfg.get("file_path", ""), "")
        self.rds_rt_file = file_path if file_path.strip() else None
        self.rds_rt_skip_words = [
            w.lower() for w in _list_of_str(rt_cfg.get("skip_words", []))
        ]

        # UECP-like A/B + burst
        self.rds_rt_ab_mode = (
            _parse_str(rt_cfg.get("ab_mode", "auto")).strip().lower() or "auto"
        )
        if self.rds_rt_ab_mode not in {"legacy", "auto", "bank"}:
            self.rds_rt_ab_mode = "auto"
        self.rds_rt_repeats = max(1, _parse_int(rt_cfg.get("repeats", 3), 3))
        self.rds_rt_gap_ms = max(0, _parse_int(rt_cfg.get("gap_ms", 60), 60))
        bank_val = rt_cfg.get("bank", None)
        self.rds_rt_bank = (int(bank_val) & 1) if bank_val is not None else None

        # Monitor
        self.monitor_health = _parse_bool(monitor.get("health", True), True)
        self.monitor_asq = _parse_bool(monitor.get("asq", True), True)
        raw_interval = monitor.get("interval_s", monitor.get("health_interval_s", 1.0))
        interval_val = _parse_float(raw_interval, 1.0)
        self.health_interval_s = float(interval_val if interval_val is not None else 1.0)
        self.recovery_attempts = _parse_int(monitor.get("recovery_attempts", 3), 3)
        self.recovery_backoff_s = float(monitor.get("recovery_backoff_s", 0.5))
        _missing = object()
        raw_ignore = monitor.get("overmod_ignore_below_dbfs", _missing)
        # Default to -5 dBFS when not provided; explicit null disables.
        if raw_ignore is None:
            self.monitor_overmod_ignore_dbfs = None
        elif raw_ignore is _missing:
            self.monitor_overmod_ignore_dbfs = -5.0
        else:
            self.monitor_overmod_ignore_dbfs = _parse_float(raw_ignore, -5.0)

        # UECP
        self.uecp_enabled = _parse_bool(uecp.get("enabled", False), False)
        self.uecp_host = _parse_str(uecp.get("host", "0.0.0.0"), "0.0.0.0")
        self.uecp_port = _parse_int(uecp.get("port", 9100), 9100)
        if not (1 <= self.uecp_port <= 65535):
            logger.warning("Invalid UECP port %s; using 9100", self.uecp_port)
            self.uecp_port = 9100
        if self.uecp_enabled and not self.rds_enabled:
            logger.warning("UECP enabled; forcing rds.enabled=true")
            self.rds_enabled = True

    @property
    def freq_10khz(self) -> int:
        """Return frequency in 10 kHz units for the device API."""
        return int(round(self.frequency_khz / 10.0))


def _effective_antenna_cap(cfg: AppConfig) -> int:
    """Return antenna capacitor value, using auto (0) when enabled."""
    return 0 if cfg.antenna_cap_auto else cfg.antenna_cap


def _effective_audio_deviation(cfg: AppConfig) -> int:
    """Return the active audio deviation based on RDS enable state."""
    if not cfg.rds_enabled and cfg.audio_dev_no_rds_hz:
        return cfg.audio_dev_no_rds_hz
    return cfg.audio_dev_hz


# ---------------------------------------------------------------------
# RT helpers
# ---------------------------------------------------------------------


def _fmt_rt(s: str, center: bool) -> str:
    """Format RT text to 32 chars, adding CR when centering is requested."""
    # Centering is signaled via CR on-device; always terminate with CR when center=True.
    if center:
        txt = s[:31]
        if not txt.endswith("\r"):
            txt = (txt + "\r")[:32]
        return txt
    return s[:32]


def _resolve_file_rt(cfg: AppConfig, macro_ctx: Dict[str, str]) -> Optional[str]:
    """Resolve RT from a file, applying macros and skip rules."""
    if not cfg.rds_rt_file:
        return None
    mt = _get_mtime(cfg.rds_rt_file)
    if mt is None:
        return None
    raw = _read_text_file(cfg.rds_rt_file)
    if not raw:
        return None
    norm = _normalize_rt_source(_apply_macros(raw, macro_ctx))
    if any(sw in norm.lower() for sw in cfg.rds_rt_skip_words):
        return None
    return _fmt_rt(norm, cfg.rds_rt_center)


def _resolve_rotation_rt(
    cfg: AppConfig, idx: int, macro_ctx: Dict[str, str]
) -> Optional[str]:
    """Resolve RT from list or fallback text."""
    if cfg.rds_rt_texts:
        txt = _apply_macros(cfg.rds_rt_texts[idx % len(cfg.rds_rt_texts)], macro_ctx)
        return _fmt_rt(txt, cfg.rds_rt_center)
    if cfg.rds_rt_text:
        txt = _apply_macros(cfg.rds_rt_text, macro_ctx)
        return _fmt_rt(txt, cfg.rds_rt_center)
    return None


def _burst_rt(
    tx: SI4713,
    text: str,
    *,
    center: bool,
    ab_mode: str,
    repeats: int,
    gap_ms: int,
    bank: Optional[int],
    status_bus: Optional["StatusBus"] = None,
) -> None:
    """Send RT bursts with A/B handling and status updates."""
    tx.set_rt_ab_mode(ab_mode)
    # First send (potential AB flip in 'auto')
    bank_used = tx.rds_set_rt(
        text,
        bank=bank if ab_mode == "bank" else None,
        cr_terminate=center,
    )
    if status_bus is not None:
        status_bus.update_rt(text, bank_used)
    logger.info("RT send bank=%s: %r", "B" if bank_used else "A", text)
    # More sends (same content => no AB flip in 'auto')
    for _ in range(max(0, repeats - 1)):
        time.sleep(gap_ms / 1000.0)
        tx.rds_set_rt(
            text,
            bank=bank if ab_mode == "bank" else None,
            cr_terminate=center,
        )


def _crc16_ccitt(data: bytes, poly: int = 0x1021, init: int = 0xFFFF) -> int:
    """Compute CRC-16/CCITT-False for UECP frames."""
    crc = init
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ poly) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc ^ 0xFFFF


def _uecp_unstuff(data: bytes) -> bytes:
    """Remove UECP byte stuffing (0xFD 00/01/02 escapes)."""
    out = bytearray()
    i = 0
    while i < len(data):
        b = data[i]
        if b != 0xFD:
            out.append(b)
            i += 1
            continue
        if i + 1 >= len(data):
            return b""
        esc = data[i + 1]
        if esc == 0x00:
            out.append(0xFD)
        elif esc == 0x01:
            out.append(0xFE)
        elif esc == 0x02:
            out.append(0xFF)
        else:
            return b""
        i += 2
    return bytes(out)


def _decode_uecp_frame(frame: bytes) -> Optional[bytes]:
    """Decode a single UECP frame into its group payload."""
    if len(frame) < 6:
        return None
    payload = _uecp_unstuff(frame)
    if len(payload) < 6:
        return None
    body = payload[:-2]
    got_crc = int.from_bytes(payload[-2:], "big")
    if _crc16_ccitt(body) != got_crc:
        return None
    if len(body) < 4:
        return None
    msg_len = body[3]
    if len(body) < 4 + msg_len:
        return None
    return body[4 : 4 + msg_len]


@dataclass
class UecpState:
    """Last applied UECP values to avoid redundant writes."""

    pi: Optional[int] = None
    pty: Optional[int] = None
    tp: Optional[bool] = None
    ta: Optional[bool] = None
    ms: Optional[bool] = None
    di: Optional[Tuple[bool, bool, bool, bool]] = None
    ps: Optional[str] = None
    pscount_set: bool = False
    rt: Optional[str] = None
    rt_bank: Optional[int] = None
    af_code: Optional[int] = None


class UecpBridge:
    """Listen for UECP frames and apply supported fields to SI4713."""

    def __init__(
        self,
        tx: SI4713,
        cfg: AppConfig,
        status_bus: Optional["StatusBus"],
        stop_event: threading.Event,
    ) -> None:
        self._tx = tx
        self._cfg = cfg
        self._status_bus = status_bus
        self._stop_event = stop_event
        self._local_stop = threading.Event()
        self._state = UecpState()
        self._lock = threading.Lock()
        self._threads: List[threading.Thread] = []
        self._tcp_sock: Optional[socket.socket] = None
        self._udp_sock: Optional[socket.socket] = None
        self._last_payloads: Dict[int, bytes] = {}

    def update_config(self, cfg: AppConfig) -> None:
        """Update config for host/port changes."""
        self._cfg = cfg

    def start(self) -> None:
        """Start TCP and UDP listeners."""
        self._local_stop.clear()
        self._start_udp()
        self._start_tcp()
        self._prime_ps()

    def _prime_ps(self) -> None:
        """Send default PI/PS once to override the SI4713 default label."""
        ps_text = "- RDS -"
        with self._lock:
            try:
                self._tx.rds_set_ps(ps_text, 0)
                self._tx.rds_set_pscount(1, 1)
                self._tx.rds_set_pi(0xFFFF)
                self._state.ps = ps_text
                self._state.pscount_set = True
                self._state.pi = 0xFFFF
                if self._status_bus is not None:
                    self._status_bus.update_ps([ps_text])
                    self._status_bus.update_ps_current(ps_text.strip())
                logger.info("UECP primed PS=%r PI=0xFFFF", ps_text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("UECP prime failed: %s", exc)

    def stop(self) -> None:
        """Stop listeners and close sockets."""
        self._local_stop.set()
        for sock in (self._tcp_sock, self._udp_sock):
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        for t in self._threads:
            t.join(timeout=1)
        self._threads = []

    def reset_state(self) -> None:
        """Clear cached UECP values so the next frames re-apply."""
        with self._lock:
            self._state = UecpState()
            self._last_payloads.clear()

    def _start_udp(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._cfg.uecp_host, self._cfg.uecp_port))
        sock.settimeout(0.5)
        self._udp_sock = sock
        t = threading.Thread(target=self._udp_loop, name="UECP-UDP", daemon=True)
        t.start()
        self._threads.append(t)

    def _start_tcp(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self._cfg.uecp_host, self._cfg.uecp_port))
        sock.listen(1)
        sock.settimeout(0.5)
        self._tcp_sock = sock
        t = threading.Thread(target=self._tcp_loop, name="UECP-TCP", daemon=True)
        t.start()
        self._threads.append(t)

    def _udp_loop(self) -> None:
        while not self._stop_event.is_set() and not self._local_stop.is_set():
            try:
                data, _addr = self._udp_sock.recvfrom(4096)  # type: ignore[arg-type]
            except socket.timeout:
                continue
            except Exception:
                break
            buf = bytearray()
            self._handle_stream(buf, data)

    def _tcp_loop(self) -> None:
        while not self._stop_event.is_set() and not self._local_stop.is_set():
            try:
                conn, _addr = self._tcp_sock.accept()  # type: ignore[union-attr]
            except socket.timeout:
                continue
            except Exception:
                break
            with conn:
                conn.settimeout(0.5)
                buf = bytearray()
                while not self._stop_event.is_set() and not self._local_stop.is_set():
                    try:
                        chunk = conn.recv(4096)
                    except socket.timeout:
                        continue
                    except Exception:
                        break
                    if not chunk:
                        break
                    self._handle_stream(buf, chunk)

    def _handle_stream(self, buf: bytearray, data: bytes) -> None:
        buf.extend(data)
        while True:
            try:
                start = buf.index(0xFE)
            except ValueError:
                buf.clear()
                break
            if start > 0:
                del buf[:start]
            try:
                end = buf.index(0xFF, 1)
            except ValueError:
                break
            frame = bytes(buf[1:end])
            del buf[: end + 1]
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("UECP frame: %s", frame.hex())
            group = _decode_uecp_frame(frame)
            if group:
                self._apply_group(group)
            elif logger.isEnabledFor(logging.DEBUG):
                logger.debug("UECP frame dropped (CRC/length)")

    def _apply_group(self, group: bytes) -> None:
        if len(group) < 3:
            return
        mec = group[0]
        ctrl1 = group[1]
        ctrl2 = group[2]
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "UECP group: mec=0x%02X ctrl1=0x%02X ctrl2=0x%02X len=%d",
                mec,
                ctrl1,
                ctrl2,
                len(group) - 3,
            )
        data = group[3:]
        payload = data[2:] if len(data) >= 2 else data
        last_payload = self._last_payloads.get(mec)
        if last_payload == data:
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("UECP mec 0x%02X unchanged; skip", mec)
            return
        with self._lock:
            if mec == 0x01:
                if len(data) >= 4:
                    pi = int.from_bytes(data[2:4], "big")
                elif len(data) >= 2:
                    pi = int.from_bytes(data[:2], "big")
                else:
                    return
                if pi != self._state.pi:
                    self._tx.rds_set_pi(pi)
                    self._state.pi = pi
                    logger.info("UECP PI set: 0x%04X", pi)
                self._last_payloads[mec] = data
            elif mec == 0x07 and payload:
                pty = int(payload[0]) & 0x1F
                if pty != self._state.pty:
                    self._tx.rds_set_pty(pty)
                    self._state.pty = pty
                    logger.info("UECP PTY set: %d", pty)
                self._last_payloads[mec] = data
            elif mec == 0x03 and payload:
                tp = bool((payload[0] >> 1) & 1)
                ta = bool(payload[0] & 1)
                if tp != self._state.tp:
                    self._tx.rds_set_tp(tp)
                    self._state.tp = tp
                    logger.info("UECP TP set: %s", tp)
                if ta != self._state.ta:
                    self._tx.rds_set_ta(ta)
                    self._state.ta = ta
                    logger.info("UECP TA set: %s", ta)
                self._last_payloads[mec] = data
            elif mec == 0x05 and payload:
                ms = bool(payload[0] & 1)
                if ms != self._state.ms:
                    self._tx.rds_set_ms_music(ms)
                    self._state.ms = ms
                    logger.info("UECP MS set: %s", ms)
                self._last_payloads[mec] = data
            elif mec == 0x04 and payload:
                flags = (
                    bool(payload[0] & 0x01),
                    bool(payload[0] & 0x02),
                    bool(payload[0] & 0x04),
                    bool(payload[0] & 0x08),
                )
                if flags != self._state.di:
                    self._tx.rds_set_di(
                        stereo=flags[0],
                        artificial_head=flags[1],
                        compressed=flags[2],
                        dynamic_pty=flags[3],
                    )
                    self._state.di = flags
                    logger.info(
                        "UECP DI set: stereo=%s artificial=%s compressed=%s dynamic=%s",
                        flags[0],
                        flags[1],
                        flags[2],
                        flags[3],
                    )
                self._last_payloads[mec] = data
            elif mec == 0x02 and payload:
                if len(data) >= 10:
                    ps_bytes = data[2:10]
                else:
                    ps_bytes = payload[:8]
                if len(ps_bytes) < 8:
                    return
                ps = ps_bytes.decode("ascii", "replace")
                if ps != self._state.ps:
                    self._tx.rds_set_ps(ps, 0)
                    if not self._state.pscount_set:
                        self._tx.rds_set_pscount(1, 1)
                        self._state.pscount_set = True
                    self._state.ps = ps
                    if self._status_bus is not None:
                        self._status_bus.update_ps([ps])
                        self._status_bus.update_ps_current(ps.strip())
                    logger.info("UECP PS set: %r", ps)
                self._last_payloads[mec] = data
            elif mec == 0x0A and len(data) >= 3:
                med: bytes
                if len(data) >= 3 and data[2] <= len(data) - 3:
                    mel = data[2]
                    med = bytes(data[3 : 3 + mel])
                else:
                    med = payload
                if not med:
                    return
                control = med[0]
                raw = med[1:]
                add_cr = False
                cr_idx = raw.find(b"\r")
                if cr_idx != -1:
                    raw = raw[:cr_idx]
                    add_cr = True
                rt_text = raw.decode("latin-1", "replace")
                rt_text = rt_text[:32].rstrip()
                bank = self._state.rt_bank or 0
                if control & 0x01:
                    bank ^= 1
                if rt_text and (
                    rt_text != self._state.rt or bank != self._state.rt_bank
                ):
                    self._tx.set_rt_ab_mode("bank")
                    self._tx.rds_set_rt(rt_text, bank=bank, cr_terminate=add_cr)
                    self._state.rt = rt_text
                    self._state.rt_bank = bank
                    if self._status_bus is not None:
                        self._status_bus.update_rt(rt_text, bank)
                    logger.info("UECP RT set (bank %d): %r", bank, rt_text)
                self._last_payloads[mec] = data
            elif mec == 0x13 and payload:
                af_code: Optional[int] = None
                variant = payload[0]
                if variant in (0x05, 0x07, 0x0F) and len(payload) >= 5:
                    af_code = int(payload[4])
                if af_code is not None and af_code != self._state.af_code:
                    self._tx.rds_set_af(af_code)
                    self._state.af_code = af_code
                    logger.info("UECP AF set: code=%d", af_code)
                self._last_payloads[mec] = data


# ---------------------------------------------------------------------
# Apply + live reconfig + recover
# ---------------------------------------------------------------------


def apply_config(
    tx: SI4713,
    cfg: AppConfig,
    config_name: str,
    status_bus: Optional["StatusBus"] = None,
    tx_enabled: bool = True,
) -> Tuple[str, str, int, float, int, float, List[str]]:
    """Apply a full config and return RT/PS rotation state."""
    # RF / audio
    cap_to_use = _effective_antenna_cap(cfg)
    tx.set_output(cfg.power if tx_enabled else 0, cap_to_use)
    if cfg.antenna_cap_auto:
        tuned_cap = tx.read_antenna_cap()
        if tuned_cap is not None:
            logger.info("Antenna cap auto-tuned -> %d", tuned_cap)
        else:
            logger.info("Antenna cap auto-tuned (value unavailable)")
    tx.set_frequency_10khz(cfg.freq_10khz)
    if status_bus is not None:
        status_bus.update_freq(cfg.frequency_khz)
        status_bus.update_tx_enabled(tx_enabled)
    tx.enable_mpx(tx_enabled)

    # Pilot/audio
    tx.set_pilot(freq_hz=19000, dev_hz=675)  # 6.75 kHz
    tx.set_audio(
        deviation_hz=_effective_audio_deviation(cfg),
        mute=not tx_enabled,
        preemph_us=cfg.preemph_us,
    )

    # Loudness & peak control
    tx.set_audio_processing(
        agc_on=False,  # Disable AGC
        limiter_on=True,  # Keep limiter to avoid clipping
        comp_thr=-30,  # Aggressive compression
        comp_att=0,  # Fastest attack
        comp_rel=2,  # Fast release
        comp_gain=15,  # High gain
        lim_rel=50,  # Fast limiter response
    )

    tx.rds_set_deviation(cfg.rds_dev_hz)  # 10 Hz units
    tx.rds_enable(cfg.rds_enabled)

    if cfg.uecp_enabled:
        rot_idx = 0
        now = time.monotonic()
        next_rotate_at = now + max(0.5, cfg.rds_rt_speed_s)
        ps_idx = 0
        next_ps_rotate = now + max(0.5, cfg.rds_ps_speed)
        return "", "uecp", rot_idx, next_rotate_at, ps_idx, next_ps_rotate, []

    # RDS flags/props
    tx.rds_set_pi(cfg.rds_pi)
    tx.rds_set_pty(cfg.rds_pty)
    tx.rds_set_tp(cfg.rds_tp)
    tx.rds_set_ta(cfg.rds_ta)
    tx.rds_set_ms_music(cfg.rds_ms_music)
    tx.rds_set_di(
        stereo=cfg.di_stereo,
        artificial_head=cfg.di_artificial_head,
        compressed=cfg.di_compressed,
        dynamic_pty=cfg.di_dynamic_pty,
    )

    # PS
    macro_ctx = _macro_context(
        config_name, freq_khz=cfg.frequency_khz, power=cfg.power
    )
    ps_slots, ps_rendered = _render_ps_slots(
        cfg.rds_ps, center=cfg.rds_ps_center, macro_ctx=macro_ctx
    )
    if ps_slots:
        tx.rds_set_ps(ps_slots[0][0], 0)
        ps_rendered = [ps_slots[0][0]]
    tx.rds_set_pscount(1, max(1, int(round(cfg.rds_ps_speed))))
    logger.info("PS set: %s", cfg.rds_ps)
    if status_bus is not None:
        status_bus.update_ps(cfg.rds_ps)
        if ps_rendered:
            status_bus.update_ps_current(ps_rendered[0].strip())

    # RT initial
    rt_text: Optional[str] = _resolve_file_rt(cfg, macro_ctx)
    source: str
    rot_idx = 0
    now = time.monotonic()
    next_rotate_at = now + max(0.5, cfg.rds_rt_speed_s)
    ps_idx = 0
    next_ps_rotate = now + max(0.5, cfg.rds_ps_speed)

    if rt_text is not None:
        source = "file"
    else:
        rt_text = _resolve_rotation_rt(cfg, rot_idx, macro_ctx) or ""
        source = f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"

    _burst_rt(
        tx,
        rt_text,
        center=cfg.rds_rt_center,
        ab_mode=cfg.rds_rt_ab_mode,
        repeats=cfg.rds_rt_repeats,
        gap_ms=cfg.rds_rt_gap_ms,
        bank=cfg.rds_rt_bank,
        status_bus=status_bus,
    )
    next_rotate_at = time.monotonic() + max(0.5, cfg.rds_rt_speed_s)

    logger.info("RT source active: %s -> %r", source, rt_text)
    return rt_text, source, rot_idx, next_rotate_at, ps_idx, next_ps_rotate, ps_rendered


def reconfigure_live(
    tx: SI4713,
    old: AppConfig,
    new: AppConfig,
    config_name: str,
    status_bus: Optional["StatusBus"] = None,
    tx_enabled: bool = True,
) -> bool:
    """Apply diffs and return True if RT should be re-burst."""
    rt_dep_changed = False
    if (
        old.power != new.power
        or old.antenna_cap != new.antenna_cap
        or old.antenna_cap_auto != new.antenna_cap_auto
    ):
        cap_to_use = _effective_antenna_cap(new)
        tx.set_output(new.power if tx_enabled else 0, cap_to_use)
        if new.antenna_cap_auto and tx_enabled:
            tuned_cap = tx.read_antenna_cap()
            if tuned_cap is not None:
                logger.info("Antenna cap auto-tuned -> %d", tuned_cap)
            else:
                logger.info("Antenna cap auto-tuned (value unavailable)")
    old_audio_dev = _effective_audio_deviation(old)
    new_audio_dev = _effective_audio_deviation(new)
    if old_audio_dev != new_audio_dev or old.preemph_us != new.preemph_us:
        tx.set_audio(
            deviation_hz=new_audio_dev,
            mute=not tx_enabled,
            preemph_us=new.preemph_us,
        )
    if old.frequency_khz != new.frequency_khz:
        tx.set_frequency_10khz(new.freq_10khz)
        if status_bus is not None:
            status_bus.update_freq(new.frequency_khz)
    if old.rds_dev_hz != new.rds_dev_hz:
        tx.rds_set_deviation(new.rds_dev_hz)
    if old.rds_enabled != new.rds_enabled:
        tx.rds_enable(new.rds_enabled)
    if new.uecp_enabled:
        return False
    if old.uecp_enabled and not new.uecp_enabled:
        rt_dep_changed = True
    if old.rds_pi != new.rds_pi:
        tx.rds_set_pi(new.rds_pi)
    if old.rds_pty != new.rds_pty:
        tx.rds_set_pty(new.rds_pty)
        rt_dep_changed = True
    if old.rds_tp != new.rds_tp:
        tx.rds_set_tp(new.rds_tp)
        rt_dep_changed = True
    if old.rds_ta != new.rds_ta:
        tx.rds_set_ta(new.rds_ta)
        rt_dep_changed = True
    if old.rds_ms_music != new.rds_ms_music:
        tx.rds_set_ms_music(new.rds_ms_music)
        rt_dep_changed = True
    if (
        old.di_stereo != new.di_stereo
        or old.di_artificial_head != new.di_artificial_head
        or old.di_compressed != new.di_compressed
        or old.di_dynamic_pty != new.di_dynamic_pty
    ):
        tx.rds_set_di(
            stereo=new.di_stereo,
            artificial_head=new.di_artificial_head,
            compressed=new.di_compressed,
            dynamic_pty=new.di_dynamic_pty,
        )
        rt_dep_changed = True
    if (
        old.rds_rt_center != new.rds_rt_center
        or old.rds_rt_ab_mode != new.rds_rt_ab_mode
        or old.rds_rt_repeats != new.rds_rt_repeats
        or old.rds_rt_gap_ms != new.rds_rt_gap_ms
        or old.rds_rt_bank != new.rds_rt_bank
        or old.rds_rt_text != new.rds_rt_text
        or old.rds_rt_texts != new.rds_rt_texts
        or old.rds_rt_file != new.rds_rt_file
        or old.rds_rt_skip_words != new.rds_rt_skip_words
    ):
        rt_dep_changed = True
    # PS
    if old.rds_ps_center != new.rds_ps_center or old.rds_ps != new.rds_ps:
        macro_ctx = _macro_context(
            config_name, freq_khz=new.frequency_khz, power=new.power
        )
        ps_slots, _rendered = _render_ps_slots(
            new.rds_ps, center=new.rds_ps_center, macro_ctx=macro_ctx
        )
        if ps_slots:
            tx.rds_set_ps(ps_slots[0][0], 0)
        tx.rds_set_pscount(1, max(1, int(round(new.rds_ps_speed))))
        if status_bus is not None:
            status_bus.update_ps(new.rds_ps)
            if ps_slots:
                status_bus.update_ps_current(ps_slots[0][0].strip())
        logger.info("PS updated: %s", new.rds_ps)
    if old.rds_ps_count != new.rds_ps_count or old.rds_ps_speed != new.rds_ps_speed:
        tx.rds_set_pscount(1, max(1, int(round(new.rds_ps_speed))))
    return rt_dep_changed


def recover_tx(tx: SI4713, cfg: AppConfig) -> bool:
    """Attempt recovery via reset and re-apply config."""
    for attempt in range(1, cfg.recovery_attempts + 1):
        logger.warning(
            "TX health failed; attempting recovery (%d/%d)...",
            attempt,
            cfg.recovery_attempts,
        )
        tx.hw_reset(RESET_PIN)
        time.sleep(0.05)
        if not tx.init(RESET_PIN, REFCLK_HZ):
            time.sleep(cfg.recovery_backoff_s * attempt)
            continue
        try:
            _rt, _src, _idx, _next, _ps_idx, _ps_next, _ps_render = apply_config(
                tx, cfg, ""
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Reconfigure failed: %s", exc)
            time.sleep(cfg.recovery_backoff_s * max(1, attempt // 2))
            continue
        if tx.is_transmitting():
            logger.info("TX recovered on attempt %d", attempt)
            return True
        time.sleep(cfg.recovery_backoff_s * max(1, attempt // 2))
    return False


def _stop_player(proc: Optional[subprocess.Popen[bytes]]) -> None:
    """Terminate the audio player process if it is running."""
    if proc is None:
        return
    try:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception:
        pass


class AudioPlayerManager:
    """Manage the external audio player process with restart backoff."""

    def __init__(self, adapter_cfg: Dict[str, Any]) -> None:
        self._adapter_cfg = adapter_cfg
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._last_cfg: Tuple[Optional[bool], Optional[str]] = (None, None)
        self._restart_backoff_s = 5.0
        self._next_restart_at = 0.0

    def set_enabled(self, enabled: bool, cfg: AppConfig) -> None:
        """Start or stop the player based on TX enabled state."""
        if enabled:
            self.refresh(cfg, force=True)
        else:
            self.stop()

    def sync(self, enabled: bool, cfg: AppConfig) -> None:
        """Apply config without forcing restarts unless required."""
        if enabled:
            self.refresh(cfg, force=False)
        else:
            self.stop()

    def refresh(self, cfg: AppConfig, *, force: bool = False) -> None:
        """(Re)start the player when the desired config changes."""
        desired = (cfg.audio_play_enabled, cfg.audio_stream_url)
        if not force and desired == self._last_cfg:
            return
        self.stop()
        self._last_cfg = desired
        if not cfg.audio_play_enabled:
            logger.info("Audio player disabled")
            return
        if not cfg.audio_stream_url:
            logger.warning("Audio player enabled but no stream_url provided")
            return
        template = str(self._adapter_cfg.get("audio_player_cmd") or "").strip()
        if not template:
            logger.warning("Audio player command not configured")
            return
        device_flag_tpl = str(
            self._adapter_cfg.get("audio_player_device_flag") or ""
        ).strip()
        device = (
            _parse_str(self._adapter_cfg.get("audio_device", "auto"), "auto") or "auto"
        )
        url_arg_tpl = str(self._adapter_cfg.get("audio_player_url_arg") or "{url}")
        logger.info(
            "Starting audio player: url=%s device=%s", cfg.audio_stream_url, device
        )

        cmd_str = ""
        cmd_parts: List[str] = []
        try:
            # Quote device/URL so shlex.split keeps them together even when they contain spaces.
            url_safe = shlex.quote(cfg.audio_stream_url)
            device_safe = shlex.quote(device)
            cmd_str = template.format(url=url_safe, device=device_safe)
            cmd_parts = shlex.split(cmd_str)
            if not cmd_parts:
                raise ValueError("audio_player_cmd produced empty command")
            if device_flag_tpl:
                flag_str = device_flag_tpl.format(device=device_safe)
                cmd_parts += shlex.split(flag_str)
            if url_arg_tpl:
                cmd_parts += shlex.split(url_arg_tpl.format(url=url_safe))
            logger.info("Audio player argv: %s", cmd_parts)
            self._proc = subprocess.Popen(
                cmd_parts, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            logger.info("Audio player started: %s", cmd_parts)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to start audio player: %s (cmd=%r)",
                exc,
                cmd_parts if cmd_parts else cmd_str,
            )

    def stop(self) -> None:
        """Stop the player and reset desired state."""
        _stop_player(self._proc)
        self._proc = None
        self._last_cfg = (None, None)

    def tick(self, now: float, tx_enabled: bool, cfg: AppConfig) -> None:
        """Restart the player if it exits while TX is enabled."""
        if self._proc is not None:
            exit_code = self._proc.poll()
            if exit_code is not None:
                logger.warning("Audio player exited (%s); scheduling restart", exit_code)
                self._proc = None
                self._last_cfg = (None, None)
                self._next_restart_at = now + self._restart_backoff_s

        if (
            self._proc is None
            and tx_enabled
            and cfg.audio_play_enabled
            and cfg.audio_stream_url
            and now >= self._next_restart_at
        ):
            self.refresh(cfg, force=True)
            if self._proc is None:
                self._next_restart_at = now + self._restart_backoff_s


class TxState(Enum):
    """High-level TX lifecycle states."""

    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass
class TxStateMachine:
    """Manage TX enable/disable transitions and state."""

    tx: SI4713
    cfg: AppConfig
    player: AudioPlayerManager
    status_bus: Optional["StatusBus"] = None
    enabled: bool = True
    state: TxState = field(init=False)

    def __post_init__(self) -> None:
        self.state = TxState.RUNNING if self.enabled else TxState.STOPPED

    def update_config(self, cfg: AppConfig) -> None:
        """Update the active configuration reference."""
        self.cfg = cfg

    def set_enabled(self, enabled: bool) -> None:
        """Apply TX on/off transition if state changes."""
        if enabled == self.enabled:
            return
        self.state = TxState.STARTING if enabled else TxState.STOPPING
        cap_to_use = _effective_antenna_cap(self.cfg)
        self.tx.enable_mpx(enabled)
        self.tx.set_output(self.cfg.power if enabled else 0, cap_to_use)
        self.tx.set_audio(
            deviation_hz=_effective_audio_deviation(self.cfg),
            mute=not enabled,
            preemph_us=self.cfg.preemph_us,
        )
        self.player.set_enabled(enabled, self.cfg)
        self.enabled = enabled
        self.state = TxState.RUNNING if enabled else TxState.STOPPED
        if self.status_bus is not None:
            self.status_bus.update_tx_enabled(enabled)


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def load_yaml_config(path: str) -> AppConfig:
    """Load a JSON config file and return AppConfig."""
    if not path.endswith(".json"):
        logger.critical("Only JSON configs are supported now.")
        raise SystemExit(2)
    with open(path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        logger.critical("Config root must be a mapping/dictionary")
        raise SystemExit(2)
    return AppConfig(raw)


def load_state(path: str) -> Dict[str, Any]:
    """Load the persisted state file as a dict."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read state %s: %s", path, exc)
        return {}


def save_state(path: str, data: Dict[str, Any]) -> None:
    """Merge and persist state data to disk."""
    payload: Dict[str, Any] = {}
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
            if isinstance(existing, dict):
                payload.update(existing)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read state for update %s: %s", path, exc)
    try:
        payload.update(data)
        dir_name = os.path.dirname(path)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to write state %s: %s", path, exc)


def _first_config_from_dir(cfg_dir: str) -> Optional[str]:
    """Return the first config file found in a directory."""
    try:
        entries = sorted(
            f
            for f in os.listdir(cfg_dir)
            if f.endswith(".json")
            and f != "state.json"
            and os.path.isfile(os.path.join(cfg_dir, f))
        )
        if entries:
            return os.path.abspath(os.path.join(cfg_dir, entries[0]))
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to scan config dir %s: %s", cfg_dir, exc)
    return None


def load_adapter_config(path: str) -> Dict[str, Any]:
    """Load adapter config from JSON or simple key/value text."""
    defaults: Dict[str, Any] = {
        "adapter": "ft232h",
        "ftdi_url": "ftdi://ftdi:232h/1",
        "ftdi_reset_pin": RESET_PIN,
        "i2c_bus": 1,
        "api_host": "0.0.0.0",
        "api_port": 5080,
        "audio_player_cmd": "ffplay -nodisp -autoexit -loglevel warning -i {url}",
        "audio_player_device_flag": "--audio-device={device}",
    }
    if not path:
        return defaults
    try:
        if path.lower().endswith(".json"):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                defaults.update(data)
            return defaults

        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.split("#", 1)[0].strip()
                if not raw or ":" not in raw:
                    continue
                key, val = [x.strip() for x in raw.split(":", 1)]
                if not key:
                    continue
                if re.fullmatch(r"-?\d+", val):
                    try:
                        val = int(val)
                    except Exception:
                        pass
                defaults[key] = val
    except FileNotFoundError:
        logger.warning("Adapter config %s not found; using defaults", path)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to load adapter config %s: %s", path, exc)
    return defaults


def main() -> None:
    """CLI entry point for the transmitter runner."""
    parser = argparse.ArgumentParser(description="SI4713 FM+RDS transmitter")
    parser.add_argument(
        "--cfg",
        type=str,
        required=False,
        default=None,
        help="Path to station configuration (.json)",
    )
    parser.add_argument(
        "--adapter-config",
        type=str,
        required=False,
        default=ADAPTER_CFG_PATH,
        help="Adapter/web defaults file (YAML-ish key/value or JSON)",
    )
    parser.add_argument(
        "--backend",
        type=str,
        choices=["auto", "rpi", "ft232h", "ft232h_blinka", "blinka"],
        default=None,
        help="Hardware backend: auto (default), rpi, ft232h (pyftdi), or ft232h_blinka",
    )
    parser.add_argument(
        "--ftdi-url",
        type=str,
        default=None,
        help="pyftdi device URL when using FT232H backend",
    )
    parser.add_argument(
        "--ftdi-reset-pin",
        type=int,
        default=None,
        help="FT232H GPIO pin (0-7) used for SI4713 RESET (default uses RESET_PIN)",
    )
    parser.add_argument(
        "--i2c-bus",
        type=int,
        default=None,
        help="I2C bus number (RPi backend only)",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=None,
        help="Start Flask status/config API on this port (optional; 0 disables)",
    )
    parser.add_argument(
        "--api-host",
        type=str,
        default=None,
        help="Host/interface for the API server",
    )
    parser.add_argument(
        "--start",
        action="store_true",
        help="Force TX on at startup (overrides saved state)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        help="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    args = parser.parse_args()

    if args.log_level:
        level = _resolve_log_level(args.log_level)
        logging.getLogger().setLevel(level)
        logger.setLevel(level)

    stop_requested = threading.Event()

    def _handle_stop(signum: int, _frame: object) -> None:
        try:
            name = signal.Signals(signum).name
        except Exception:
            name = str(signum)
        logger.warning("Stop requested (%s)", name)
        stop_requested.set()

    for sig_name in ("SIGTERM", "SIGINT", "SIGHUP", "SIGQUIT"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _handle_stop)
        except Exception:
            pass

    adapter_cfg = load_adapter_config(args.adapter_config)

    state = load_state(STATE_PATH)
    state_tx_enabled: Optional[bool] = None
    if isinstance(state, dict):
        raw_tx_enabled = state.get("tx_enabled")
        if isinstance(raw_tx_enabled, bool):
            state_tx_enabled = raw_tx_enabled
    cfg_from_state = state.get("config_path") if isinstance(state, dict) else None
    initial_tx_enabled = state_tx_enabled if state_tx_enabled is not None else True
    if args.start:
        if state_tx_enabled is False:
            logger.info("Start override requested; enabling TX.")
        initial_tx_enabled = True

    cfg_path = os.path.abspath(args.cfg) if args.cfg else None
    if cfg_path is None and cfg_from_state:
        candidate = os.path.abspath(str(cfg_from_state))
        if os.path.exists(candidate):
            cfg_path = candidate
        else:
            logger.warning("State config missing: %s", candidate)

    if cfg_path is None and os.path.exists(DEFAULT_CFG_PATH):
        cfg_path = os.path.abspath(DEFAULT_CFG_PATH)
        logger.info("Using default config: %s", cfg_path)

    if cfg_path is None:
        cfg_path = _first_config_from_dir(
            os.path.join(os.path.dirname(__file__), "cfg")
        )
        if cfg_path:
            logger.info("Using first available config: %s", cfg_path)

    if not cfg_path or not os.path.exists(cfg_path):
        logger.critical("No config available. Provide one with --cfg.")
        sys.exit(2)

    cfg = load_yaml_config(cfg_path)
    last_cfg_mtime = _get_mtime(cfg_path)
    cfg_name = os.path.splitext(os.path.basename(cfg_path))[0]
    macro_cache = MacroContextCache(cfg_name, cfg.frequency_khz, cfg.power)
    rt_macros_used = _rt_macros_possible(cfg)
    live_reload_enabled = _parse_bool(
        os.getenv("SI4713_LIVE_RELOAD", "0"), False
    )

    status_bus: Optional["StatusBus"] = None
    api_thread: Optional[threading.Thread] = None
    backend = (
        args.backend
        or os.getenv("SI4713_BACKEND")
        or str(adapter_cfg.get("adapter") or "auto")
    )
    ftdi_url = (
        args.ftdi_url
        or os.getenv("SI4713_FT232H_URL")
        or str(adapter_cfg.get("ftdi_url") or "ftdi://ftdi:232h/1")
    )
    ftdi_reset_pin = (
        args.ftdi_reset_pin
        if args.ftdi_reset_pin is not None
        else _parse_int(
            os.getenv(
                "SI4713_FT232H_RESET_PIN", adapter_cfg.get("ftdi_reset_pin", RESET_PIN)
            ),
            RESET_PIN,
        )
    )
    i2c_bus = (
        args.i2c_bus
        if args.i2c_bus is not None
        else _parse_int(os.getenv("SI4713_I2C_BUS", adapter_cfg.get("i2c_bus", 1)), 1)
    )
    api_port_arg = (
        args.api_port if args.api_port is not None else adapter_cfg.get("api_port")
    )
    api_host_arg = args.api_host or adapter_cfg.get("api_host")

    # Ensure Blinka is enabled automatically when requested
    if backend in {"ft232h_blinka", "blinka"}:
        os.environ["BLINKA_FT232H"] = "1"
    else:
        os.environ.pop("BLINKA_FT232H", None)
    if api_port_arg == 0:
        api_port_arg = None

    # Resolve API settings from state if CLI not provided
    if api_port_arg is None and isinstance(state, dict):
        api_port_arg = state.get("api_port")
        if api_port_arg == 0:
            api_port_arg = None
        api_host_arg = api_host_arg or state.get("api_host")
        api_enabled = bool(state.get("api_enabled", False))
    else:
        api_enabled = api_port_arg is not None

    if api_enabled and api_port_arg:
        try:
            from web import LogBus, StatusBus, attach_log_handler, create_app, run_app

            logging.getLogger("werkzeug").setLevel(logging.WARNING)
        except Exception as exc:  # noqa: BLE001
            logger.error("API requested but Flask is not available: %s", exc)
        else:
            status_bus = StatusBus()
            log_bus = LogBus()
            attach_log_handler(log_bus)
            status_bus.set_config_path(cfg_path)
            status_bus.update_tx_enabled(initial_tx_enabled)
            api_app = create_app(
                status_bus,
                os.path.dirname(os.path.abspath(cfg_path)),
                STATE_PATH,
                log_bus,
            )
            api_thread = threading.Thread(
                target=run_app,
                args=(api_app, api_host_arg or "0.0.0.0", api_port_arg),
                daemon=True,
            )
            api_thread.start()
            logger.info(
                "API server started at http://%s:%s",
                api_host_arg or "0.0.0.0",
                api_port_arg,
            )
            # Persist state
            save_state(
                STATE_PATH,
                {
                    "config_path": cfg_path,
                    "api_enabled": True,
                    "api_port": api_port_arg,
                    "api_host": api_host_arg or "0.0.0.0",
                    "tx_enabled": initial_tx_enabled,
                },
            )

    tx = SI4713(
        i2c_bus=i2c_bus,
        backend=backend,
        ftdi_url=ftdi_url,
        ftdi_reset_pin=ftdi_reset_pin,
    )
    tx.set_stop_event(stop_requested)

    # Update state once we have successfully instantiated TX (even if API is off)
    if isinstance(state, dict):
        save_state(
            STATE_PATH,
            {
                "config_path": cfg_path,
                "api_enabled": api_enabled,
                "api_port": api_port_arg if api_port_arg else 0,
                "api_host": api_host_arg or "0.0.0.0",
                "tx_enabled": initial_tx_enabled,
            },
        )

    last_rt: Optional[str]
    rt_source: str
    rot_idx: int
    next_rotate_at: float
    ps_idx: int
    next_ps_rotate: float
    last_ps_render: List[str] = []
    ps_macros_used = False
    next_ps_macro_refresh: float = float("inf")
    player = AudioPlayerManager(adapter_cfg)
    file_mtime: Optional[float] = _get_mtime(cfg.rds_rt_file)

    def apply_new_config(
        new_cfg_path: str,
        tx_is_enabled: bool,
    ) -> Tuple[
        AppConfig,
        float,
        str,
        str,
        int,
        float,
        Optional[float],
        str,
        int,
        float,
        List[str],
        str,
    ]:
        new_cfg_path = os.path.abspath(new_cfg_path)
        new_cfg = load_yaml_config(new_cfg_path)
        new_cfg_mtime = _get_mtime(new_cfg_path) or time.time()
        cfg_nm = os.path.splitext(os.path.basename(new_cfg_path))[0]
        rt_text, rt_src, r_idx, nxt, ps_idx, ps_next, ps_render = apply_config(
            tx, new_cfg, cfg_nm, status_bus=status_bus, tx_enabled=tx_is_enabled
        )
        file_mt = _get_mtime(new_cfg.rds_rt_file)
        if status_bus is not None:
            status_bus.set_config_path(new_cfg_path)
        return (
            new_cfg,
            new_cfg_mtime,
            rt_text,
            rt_src,
            r_idx,
            nxt,
            file_mt,
            new_cfg_path,
            ps_idx,
            ps_next,
            ps_render,
            cfg_nm,
        )

    def _update_uecp_bridge(new_cfg: AppConfig) -> None:
        nonlocal uecp_bridge, uecp_sig
        new_sig = (new_cfg.uecp_enabled, new_cfg.uecp_host, new_cfg.uecp_port)
        if new_sig == uecp_sig:
            if uecp_bridge is not None:
                uecp_bridge.update_config(new_cfg)
            return
        if uecp_bridge is not None:
            uecp_bridge.stop()
            uecp_bridge = None
        if new_cfg.uecp_enabled:
            uecp_bridge = UecpBridge(tx, new_cfg, status_bus, stop_requested)
            uecp_bridge.start()
        uecp_sig = new_sig

    tx_state: Optional[TxStateMachine] = None
    uecp_bridge: Optional[UecpBridge] = None
    uecp_sig: Tuple[bool, str, int] = (False, "", 0)
    try:
        if not tx.init(RESET_PIN, REFCLK_HZ):
            logger.error("Init failed")
            tx.hw_reset(RESET_PIN)
            sys.exit(1)

        (
            last_rt,
            rt_source,
            rot_idx,
            next_rotate_at,
            ps_idx,
            next_ps_rotate,
            last_ps_render,
        ) = apply_config(
            tx, cfg, cfg_name, status_bus=status_bus, tx_enabled=initial_tx_enabled
        )
        tx_state = TxStateMachine(
            tx=tx,
            cfg=cfg,
            player=player,
            status_bus=status_bus,
            enabled=initial_tx_enabled,
        )
        ps_macros_used = any(_has_macros(ps) for ps in cfg.rds_ps)
        rt_macros_used = _rt_macros_possible(cfg)
        if cfg.uecp_enabled:
            ps_macros_used = False
            rt_macros_used = False
        next_ps_macro_refresh = (
            time.monotonic() + 60.0 if ps_macros_used else float("inf")
        )
        player.set_enabled(tx_state.enabled, cfg)
        if status_bus is not None:
            status_bus.set_config_path(cfg_path)
            status_bus.update_tx_enabled(tx_state.enabled)
        save_state(STATE_PATH, {"tx_enabled": tx_state.enabled})
        logger.info("Loaded config: %s", cfg_path)
        _update_uecp_bridge(cfg)

        if cfg.monitor_health:
            if tx_state.enabled:
                if tx.is_transmitting():
                    logger.info("TX is up at %.2f MHz", cfg.frequency_khz / 1000.0)
                else:
                    logger.error("TX not running after setup")
                    if not recover_tx(tx, cfg):
                        logger.critical("TX failed to start after recovery attempts")
                        tx.hw_reset(RESET_PIN)
                        sys.exit(1)
                    if uecp_bridge is not None:
                        uecp_bridge.reset_state()
            else:
                logger.info("TX disabled; skipping health check")
        else:
            logger.info("Health monitoring disabled; not verifying TX up/down")

        loop_max_sleep_s = 0.25
        cfg_poll_s = 0.5
        rt_file_poll_s = 0.5
        now = time.monotonic()
        next_monitor_tick = now + max(0.1, cfg.health_interval_s)
        next_cfg_poll = now + cfg_poll_s if live_reload_enabled else float("inf")
        next_rt_file_poll = now + rt_file_poll_s
        player_check_s = 0.5
        next_player_check = now + player_check_s
        health_failures = 0
        health_failure_limit = 3

        while True:
            if stop_requested.is_set():
                logger.info("Stopping main loop (will assert RESET to stop TX).")
                break
            now = time.monotonic()
            # Config switch requested via API
            if status_bus is not None:
                pending_cfg = status_bus.pop_pending_config()
                if not pending_cfg:
                    current_from_api = status_bus.current_config_path()
                    if current_from_api and current_from_api != cfg_path:
                        pending_cfg = current_from_api
                        logger.info(
                            "Config path differs (no pending flag). Current=%s, Requested=%s",
                            cfg_path,
                            current_from_api,
                        )
                if pending_cfg:
                    logger.info("Config switch detected: %s", pending_cfg)
                    try:
                        (
                            cfg,
                            last_cfg_mtime,
                            last_rt,
                            rt_source,
                            rot_idx,
                            next_rotate_at,
                            file_mtime,
                            cfg_path,
                            ps_idx,
                            next_ps_rotate,
                            last_ps_render,
                            cfg_name,
                        ) = apply_new_config(
                            pending_cfg, tx_state.enabled if tx_state else True
                        )
                        now = time.monotonic()
                        ps_macros_used = any(_has_macros(ps) for ps in cfg.rds_ps)
                        rt_macros_used = _rt_macros_possible(cfg)
                        if cfg.uecp_enabled:
                            ps_macros_used = False
                            rt_macros_used = False
                        macro_cache.set_config(cfg_name, cfg.frequency_khz, cfg.power)
                        next_ps_macro_refresh = (
                            now + 60.0 if ps_macros_used else float("inf")
                        )
                        if tx_state is not None:
                            tx_state.update_config(cfg)
                            player.sync(tx_state.enabled, cfg)
                        _update_uecp_bridge(cfg)
                        logger.info(
                            "Switched to config: %s (freq=%.2f MHz, PS=%s)",
                            cfg_path,
                            cfg.frequency_khz / 1000.0,
                            cfg.rds_ps,
                        )
                        logger.info("Loaded config: %s", cfg_path)
                        # Persist new active config + API settings
                        save_state(
                            STATE_PATH,
                            {
                                "config_path": cfg_path,
                                "api_enabled": api_enabled,
                                "api_port": api_port_arg if api_port_arg else 0,
                                "api_host": api_host_arg or "0.0.0.0",
                                "tx_enabled": tx_state.enabled if tx_state else True,
                            },
                        )
                        next_monitor_tick = now + max(0.1, cfg.health_interval_s)
                        next_cfg_poll = (
                            now + cfg_poll_s if live_reload_enabled else float("inf")
                        )
                        next_rt_file_poll = now + rt_file_poll_s
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to switch config %s: %s", pending_cfg, exc)

            # Config reload requested via API (diff-only apply)
            if status_bus is not None and status_bus.pop_pending_reload():
                logger.info("Config reload requested: %s", cfg_path)
                try:
                    new_cfg = load_yaml_config(cfg_path)
                    rt_dep_changed = reconfigure_live(
                        tx,
                        cfg,
                        new_cfg,
                        cfg_name,
                        status_bus,
                        tx_state.enabled if tx_state else True,
                    )
                    cfg = new_cfg
                    last_cfg_mtime = _get_mtime(cfg_path) or time.time()
                    ps_macros_used = any(_has_macros(ps) for ps in cfg.rds_ps)
                    rt_macros_used = _rt_macros_possible(cfg)
                    if cfg.uecp_enabled:
                        ps_macros_used = False
                        rt_macros_used = False
                    macro_cache.set_config(cfg_name, cfg.frequency_khz, cfg.power)
                    macro_ctx = (
                        macro_cache.get()
                        if (ps_macros_used or rt_macros_used)
                        else _EMPTY_MACRO_CTX
                    )
                    _slots, last_ps_render = _render_ps_slots(
                        cfg.rds_ps, center=cfg.rds_ps_center, macro_ctx=macro_ctx
                    )
                    next_ps_macro_refresh = (
                        time.monotonic() + 60.0 if ps_macros_used else float("inf")
                    )
                    if tx_state is not None:
                        tx_state.update_config(cfg)
                        player.sync(tx_state.enabled, cfg)
                    _update_uecp_bridge(cfg)
                    if cfg.uecp_enabled:
                        rt_source = "uecp"
                        last_rt = ""

                    if not cfg.uecp_enabled:
                        candidate_file = _resolve_file_rt(cfg, macro_ctx)
                        if candidate_file is not None:
                            candidate = candidate_file
                            new_src = "file"
                        else:
                            candidate = _resolve_rotation_rt(cfg, rot_idx, macro_ctx) or ""
                            new_src = (
                                f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"
                            )

                        if rt_dep_changed or candidate != last_rt or new_src != rt_source:
                            rt_source = new_src
                            last_rt = candidate or ""
                            rt_bank = cfg.rds_rt_bank
                            if candidate:
                                _burst_rt(
                                    tx,
                                    candidate,
                                    center=cfg.rds_rt_center,
                                    ab_mode=cfg.rds_rt_ab_mode,
                                    repeats=cfg.rds_rt_repeats,
                                    gap_ms=cfg.rds_rt_gap_ms,
                                    bank=rt_bank,
                                    status_bus=status_bus,
                                )
                                if status_bus is not None:
                                    status_bus.update_ps(cfg.rds_ps)
                                    status_bus.update_ps_current(
                                        cfg.rds_ps[0] if cfg.rds_ps else ""
                                    )
                                logger.info(
                                    "RT applied on config reload: %s -> %s: %r",
                                    cfg_path,
                                    new_src,
                                    candidate,
                                )
                    next_cfg_poll = (
                        time.monotonic() + cfg_poll_s if live_reload_enabled else float("inf")
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("Failed to reload config: %s", exc)

            # TX on/off toggle
            if status_bus is not None:
                pending_tx = status_bus.pop_pending_tx()
                if (
                    pending_tx is not None
                    and tx_state is not None
                    and pending_tx != tx_state.enabled
                ):
                    tx_state.set_enabled(pending_tx)
                    save_state(STATE_PATH, {"tx_enabled": tx_state.enabled})
                    logger.info(
                        "TX %s via UI toggle (stream %s)",
                        "enabled" if tx_state.enabled else "disabled",
                        "stopped" if not tx_state.enabled else "active",
                    )

            if now >= next_player_check:
                next_player_check = now + player_check_s
                if tx_state is not None:
                    player.tick(now, tx_state.enabled, cfg)

                    macro_cache.set_config(cfg_name, cfg.frequency_khz, cfg.power)
                    macro_ctx = (
                        macro_cache.get()
                        if (ps_macros_used or rt_macros_used)
                        else _EMPTY_MACRO_CTX
                    )

            # Health/ASQ monitoring runs on its own interval (cfg.health_interval_s).
            if now >= next_monitor_tick:
                if (
                    cfg.monitor_health
                    and tx_state is not None
                    and tx_state.enabled
                ):
                    status = tx.tx_status()
                    if status is None:
                        health_failures += 1
                    else:
                        _freq_10khz, power_level, _overmod, _antcap = status
                        if power_level > 0:
                            health_failures = 0
                        else:
                            health_failures += 1

                    if health_failures >= health_failure_limit:
                        logger.error("TX dropped!")
                        if not recover_tx(tx, cfg):
                            logger.critical("Unrecoverable TX failure; stopping")
                            tx.hw_reset(RESET_PIN)
                            sys.exit(2)
                        if uecp_bridge is not None:
                            uecp_bridge.reset_state()
                        health_failures = 0
                else:
                    health_failures = 0

                if cfg.monitor_asq:
                    overmod, inlvl = tx.read_asq()
                    if (
                        overmod
                        and cfg.monitor_overmod_ignore_dbfs is not None
                        and inlvl <= cfg.monitor_overmod_ignore_dbfs
                    ):
                        overmod = False
                    logger.info(
                        "Input Level: %d dBFS%s",
                        inlvl,
                        "   OVERMOD!!!" if overmod else "",
                    )

                next_monitor_tick = now + max(0.1, cfg.health_interval_s)

            # PS macro refresh (e.g., time/date) once per minute
            if ps_macros_used and now >= next_ps_macro_refresh:
                ps_slots, _rendered = _render_ps_slots(
                    cfg.rds_ps, center=cfg.rds_ps_center, macro_ctx=macro_ctx
                )
                if ps_slots:
                    tx.rds_set_ps(ps_slots[0][0], 0)
                    tx.rds_set_pscount(1, max(1, int(round(cfg.rds_ps_speed))))
                    last_ps_render = [ps_slots[0][0]]
                    if status_bus is not None:
                        status_bus.update_ps_current(ps_slots[0][0].strip())
                next_ps_macro_refresh = now + 60.0

            # Config hot-reload
            if live_reload_enabled and now >= next_cfg_poll:
                try:
                    mtime = _get_mtime(cfg_path)
                    if (
                        mtime is not None
                        and last_cfg_mtime is not None
                        and mtime > last_cfg_mtime
                    ):
                        logger.info("Config changed, reloading live: %s", cfg_path)
                        new_cfg = load_yaml_config(cfg_path)

                        # Apply diffs
                        rt_dep_changed = reconfigure_live(
                            tx,
                            cfg,
                            new_cfg,
                            cfg_name,
                            status_bus,
                            tx_state.enabled if tx_state else True,
                        )
                        cfg = new_cfg
                        last_cfg_mtime = mtime
                        ps_macros_used = any(_has_macros(ps) for ps in cfg.rds_ps)
                        rt_macros_used = _rt_macros_possible(cfg)
                        if cfg.uecp_enabled:
                            ps_macros_used = False
                            rt_macros_used = False
                        macro_ctx = (
                            macro_cache.get()
                            if (ps_macros_used or rt_macros_used)
                            else _EMPTY_MACRO_CTX
                        )
                        _slots, last_ps_render = _render_ps_slots(
                            cfg.rds_ps, center=cfg.rds_ps_center, macro_ctx=macro_ctx
                        )
                        next_ps_macro_refresh = (
                            time.monotonic() + 60.0 if ps_macros_used else float("inf")
                        )
                        if tx_state is not None:
                            tx_state.update_config(cfg)
                            player.sync(tx_state.enabled, cfg)
                        _update_uecp_bridge(cfg)
                        if cfg.uecp_enabled:
                            rt_source = "uecp"
                            last_rt = ""

                        if not cfg.uecp_enabled:
                            # Re-evaluate RT source & push if changed or deps changed
                            candidate_file = _resolve_file_rt(cfg, macro_ctx)
                            if candidate_file is not None:
                                candidate = candidate_file
                                new_src = "file"
                            else:
                                candidate = (
                                    _resolve_rotation_rt(cfg, rot_idx, macro_ctx) or ""
                                )
                                new_src = (
                                    f"list[{rot_idx}]"
                                    if cfg.rds_rt_texts
                                    else "fallback"
                                )

                            if (
                                rt_dep_changed
                                or candidate != last_rt
                                or new_src != rt_source
                            ):
                                _burst_rt(
                                    tx,
                                    candidate,
                                    center=cfg.rds_rt_center,
                                    ab_mode=cfg.rds_rt_ab_mode,
                                    repeats=cfg.rds_rt_repeats,
                                    gap_ms=cfg.rds_rt_gap_ms,
                                    bank=cfg.rds_rt_bank,
                                    status_bus=status_bus,
                                )
                                logger.info(
                                    "RT applied on config reload: %s -> %s: %r",
                                    rt_source,
                                    new_src,
                                    candidate,
                                )
                                last_rt = candidate
                                rt_source = new_src

                        now = time.monotonic()
                        next_rotate_at = now + max(0.5, cfg.rds_rt_speed_s)
                        next_monitor_tick = now + max(0.1, cfg.health_interval_s)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Failed to reload config: %s", exc)
                finally:
                    next_cfg_poll = time.monotonic() + cfg_poll_s

            # RT file watcher
            if not cfg.uecp_enabled and now >= next_rt_file_poll:
                current_mtime = _get_mtime(cfg.rds_rt_file) if cfg.rds_rt_file else None

                if cfg.rds_rt_file and current_mtime is not None and current_mtime != file_mtime:
                    candidate = _resolve_file_rt(cfg, macro_ctx)
                    if candidate is not None:
                        if candidate != last_rt or rt_source != "file":
                            _burst_rt(
                                tx,
                                candidate,
                                center=cfg.rds_rt_center,
                                ab_mode=cfg.rds_rt_ab_mode,
                                repeats=cfg.rds_rt_repeats,
                                gap_ms=cfg.rds_rt_gap_ms,
                                bank=cfg.rds_rt_bank,
                                status_bus=status_bus,
                            )
                        logger.info("RT source switch: %s -> file", rt_source)
                        rt_source = "file"
                        last_rt = candidate
                        file_mtime = current_mtime
                    else:
                        alt = _resolve_rotation_rt(cfg, rot_idx, macro_ctx) or ""
                        if alt != last_rt or rt_source == "file":
                            _burst_rt(
                                tx,
                                alt,
                                center=cfg.rds_rt_center,
                                ab_mode=cfg.rds_rt_ab_mode,
                                repeats=cfg.rds_rt_repeats,
                                gap_ms=cfg.rds_rt_gap_ms,
                                bank=cfg.rds_rt_bank,
                                status_bus=status_bus,
                            )
                            new_src = (
                                f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"
                            )
                            logger.info("RT source switch: file -> %s", new_src)
                            rt_source = new_src
                            last_rt = alt
                        file_mtime = current_mtime

                if cfg.rds_rt_file and current_mtime is None and file_mtime is not None:
                    alt = _resolve_rotation_rt(cfg, rot_idx, macro_ctx) or ""
                    if alt != last_rt or rt_source == "file":
                        _burst_rt(
                            tx,
                            alt,
                            center=cfg.rds_rt_center,
                            ab_mode=cfg.rds_rt_ab_mode,
                            repeats=cfg.rds_rt_repeats,
                            gap_ms=cfg.rds_rt_gap_ms,
                            bank=cfg.rds_rt_bank,
                            status_bus=status_bus,
                        )
                        new_src = f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"
                        logger.info("RT source switch: file -> %s", new_src)
                        rt_source = new_src
                        last_rt = alt
                    file_mtime = current_mtime

                next_rt_file_poll = time.monotonic() + rt_file_poll_s

            now = time.monotonic()
            # RT rotation tick (only when file is not active)
            if (
                not cfg.uecp_enabled
                and rt_source != "file"
                and cfg.rds_rt_texts
                and now >= next_rotate_at
            ):
                rot_idx = (rot_idx + 1) % len(cfg.rds_rt_texts)
                candidate = _resolve_rotation_rt(cfg, rot_idx, macro_ctx) or ""
                if candidate != last_rt or not rt_source.startswith("list["):
                    _burst_rt(
                        tx,
                        candidate,
                        center=cfg.rds_rt_center,
                        ab_mode=cfg.rds_rt_ab_mode,
                        repeats=cfg.rds_rt_repeats,
                        gap_ms=cfg.rds_rt_gap_ms,
                        bank=cfg.rds_rt_bank,
                        status_bus=status_bus,
                    )
                    logger.info("RT rotate -> list[%d]: %r", rot_idx, candidate)
                    rt_source = f"list[{rot_idx}]"
                    last_rt = candidate
                next_rotate_at = now + max(0.5, cfg.rds_rt_speed_s)

            # PS rotation (software-timed, overrides SI4713 internal rotation)
            if (
                not cfg.uecp_enabled
                and cfg.rds_ps
                and len(cfg.rds_ps) > 1
                and now >= next_ps_rotate
            ):
                ps_idx = (ps_idx + 1) % len(cfg.rds_ps)
                ps_txt = cfg.rds_ps[ps_idx] or ""
                if ps_macros_used:
                    ps_txt = _apply_macros(ps_txt, macro_ctx)
                ps_text8 = (
                    _center_fixed(ps_txt, 8) if cfg.rds_ps_center else ps_txt[:8].ljust(8)
                )
                tx.rds_set_ps(ps_text8, 0)
                tx.rds_set_pscount(1, max(1, int(round(cfg.rds_ps_speed))))
                last_ps_render = [ps_text8]
                if status_bus is not None:
                    status_bus.update_ps_current(ps_text8.strip())
                logger.info("PS rotate -> list[%d]: %s", ps_idx, ps_text8.strip())
                next_ps_rotate = now + max(0.5, cfg.rds_ps_speed)

            now = time.monotonic()
            next_due = min(
                next_monitor_tick,
                next_cfg_poll,
                next_rt_file_poll,
                next_ps_macro_refresh,
                next_rotate_at
                if (rt_source != "file" and cfg.rds_rt_texts)
                else float("inf"),
                next_ps_rotate
                if (cfg.rds_ps and len(cfg.rds_ps) > 1)
                else float("inf"),
            )
            sleep_s = max(0.05, min(loop_max_sleep_s, next_due - now))
            try:
                time.sleep(sleep_s)
            except InterruptedError:
                pass

    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as exc:  # noqa: BLE001
        logger.error("Fatal error: %s", exc)
    finally:
        if tx_state is not None:
            save_state(
                STATE_PATH,
                {
                    "config_path": cfg_path,
                    "api_enabled": api_enabled,
                    "api_port": api_port_arg if api_port_arg else 0,
                    "api_host": api_host_arg or "0.0.0.0",
                    "tx_enabled": tx_state.enabled,
                },
            )
        player.stop()
        try:
            tx.hw_reset(RESET_PIN)
        finally:
            tx.close()
        logger.info("Cleanup done, TX stopped")


if __name__ == "__main__":
    main()
