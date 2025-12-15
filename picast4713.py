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
import shlex
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from si4713 import SI4713

if TYPE_CHECKING:
    from web import StatusBus

# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
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
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return default


def _parse_bool(value: Any, default: bool) -> bool:
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
    return str(value) if isinstance(value, (str, int, float)) else default


def _list_of_str(v: Any) -> List[str]:
    if not isinstance(v, list):
        return []
    return [str(x) for x in v]


def _get_mtime(path: Optional[str]) -> Optional[float]:
    if not path:
        return None
    try:
        return os.path.getmtime(path)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _read_text_file(path: str, max_bytes: int = 8192) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = fh.read(max_bytes)
        return data.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    except Exception as exc:  # noqa: BLE001
        logger.error("RT file read failed (%s): %s", path, exc)
        return None


def _enforce(cond: bool, msg: str) -> None:
    if not cond:
        logger.critical("Config error: %s", msg)
        raise SystemExit(2)


def _center_fixed(s: str, width: int) -> str:
    if len(s) >= width:
        return s[:width]
    pad = width - len(s)
    left = (pad + 1) // 2
    right = pad - left
    return (" " * left) + s + (" " * right)


def _normalize_rt_source(raw: str) -> str:
    line = next((ln for ln in raw.split("\n") if ln.strip()), "")
    return " ".join(line.split())


_MACRO_PATTERN = re.compile(r"{(time|date|datetime|config)}", re.IGNORECASE)


def _macro_context(config_name: str, now: Optional[float] = None) -> Dict[str, str]:
    ts = time.localtime(now or time.time())
    base = os.path.splitext(os.path.basename(config_name or ""))[0]
    return {
        "time": time.strftime("%H:%M", ts),
        "date": time.strftime("%Y-%m-%d", ts),
        "datetime": time.strftime("%Y-%m-%d %H:%M:%S", ts),
        "config": base or config_name or "",
    }


def _apply_macros(text: str, ctx: Dict[str, str]) -> str:
    if not text:
        return ""

    def _repl(match: re.Match[str]) -> str:
        key = match.group(1).lower()
        return ctx.get(key, match.group(0))

    return _MACRO_PATTERN.sub(_repl, text)


def _has_macros(text: str) -> bool:
    return bool(text and _MACRO_PATTERN.search(text))


def _render_ps_slots(
    ps: List[str], center: bool, macro_ctx: Dict[str, str]
) -> Tuple[List[Tuple[str, int]], List[str]]:
    slots: List[Tuple[str, int]] = []
    rendered: List[str] = []
    for idx, item in enumerate(ps):
        txt = _apply_macros(item or "", macro_ctx)
        text8 = _center_fixed(txt, 8) if center else txt[:8].ljust(8)
        slots.append((text8, idx))
        rendered.append(text8)
    return slots, rendered


# ---------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------


class AppConfig:
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
    audio_play_enabled: bool
    audio_stream_url: str
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
    rds_ps_speed: int

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

    def __init__(self, raw: Dict[str, Any]) -> None:
        _enforce(isinstance(raw, dict), "root must be a mapping")

        rf = raw.get("rf", {})
        rds = raw.get("rds", {})
        monitor = raw.get("monitor", {})

        _enforce(isinstance(rf, dict), "rf must be a mapping")
        _enforce(isinstance(rds, dict), "rds must be a mapping")

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
        self.rds_ps_speed = _parse_int(rds.get("ps_speed", 10), 10)
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
        self.health_interval_s = float(monitor.get("interval_s", 1.0))
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

    @property
    def freq_10khz(self) -> int:
        return int(round(self.frequency_khz / 10.0))


# ---------------------------------------------------------------------
# RT helpers
# ---------------------------------------------------------------------


def _fmt_rt(s: str, center: bool) -> str:
    # Centering is signaled via CR on-device; always terminate with CR when center=True.
    if center:
        txt = s[:31]
        if not txt.endswith("\r"):
            txt = (txt + "\r")[:32]
        return txt
    return s[:32]


def _resolve_file_rt(cfg: AppConfig, macro_ctx: Dict[str, str]) -> Optional[str]:
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
    """
    UECP-like: first send may flip AB (auto) if content differs; then repeat same bank.
    """
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


# ---------------------------------------------------------------------
# Apply + live reconfig + recover
# ---------------------------------------------------------------------


def apply_config(
    tx: SI4713,
    cfg: AppConfig,
    config_name: str,
    status_bus: Optional["StatusBus"] = None,
) -> Tuple[str, str, int, float, int, float, List[str]]:
    # RF / audio
    cap_to_use = 0 if cfg.antenna_cap_auto else cfg.antenna_cap
    tx.set_output(cfg.power, cap_to_use)
    if cfg.antenna_cap_auto:
        tuned_cap = tx.read_antenna_cap()
        if tuned_cap is not None:
            logger.info("Antenna cap auto-tuned -> %d", tuned_cap)
        else:
            logger.info("Antenna cap auto-tuned (value unavailable)")
    tx.set_frequency_10khz(cfg.freq_10khz)
    tx.enable_mpx(True)

    # Pilot/audio
    tx.set_pilot(freq_hz=19000, dev_hz=675)  # 6.75 kHz
    audio_dev = (
        cfg.audio_dev_no_rds_hz
        if not cfg.rds_enabled and cfg.audio_dev_no_rds_hz
        else cfg.audio_dev_hz
    )
    tx.set_audio(deviation_hz=audio_dev, mute=False, preemph_us=cfg.preemph_us)

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

    # RDS flags/props
    tx.rds_set_pi(cfg.rds_pi)
    tx.rds_set_pty(cfg.rds_pty)
    tx.rds_set_deviation(cfg.rds_dev_hz)  # 10 Hz units
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
    macro_ctx = _macro_context(config_name)
    ps_slots, ps_rendered = _render_ps_slots(
        cfg.rds_ps, center=cfg.rds_ps_center, macro_ctx=macro_ctx
    )
    for text8, slot in ps_slots:
        tx.rds_set_ps(text8, slot)
    tx.rds_set_pscount(max(1, cfg.rds_ps_count), max(1, cfg.rds_ps_speed))
    tx.rds_enable(cfg.rds_enabled)
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

    logger.info("RT source active: %s -> %r", source, rt_text)
    return rt_text, source, rot_idx, next_rotate_at, ps_idx, next_ps_rotate, ps_rendered


def reconfigure_live(
    tx: SI4713,
    old: AppConfig,
    new: AppConfig,
    config_name: str,
    status_bus: Optional["StatusBus"] = None,
) -> bool:
    rt_dep_changed = False
    if (
        old.power != new.power
        or old.antenna_cap != new.antenna_cap
        or old.antenna_cap_auto != new.antenna_cap_auto
        or old.audio_dev_hz != new.audio_dev_hz
        or old.audio_dev_no_rds_hz != new.audio_dev_no_rds_hz
        or old.rds_enabled != new.rds_enabled
    ):
        cap_to_use = 0 if new.antenna_cap_auto else new.antenna_cap
        tx.set_output(new.power, cap_to_use)
        if new.antenna_cap_auto:
            tuned_cap = tx.read_antenna_cap()
            if tuned_cap is not None:
                logger.info("Antenna cap auto-tuned -> %d", tuned_cap)
            else:
                logger.info("Antenna cap auto-tuned (value unavailable)")
        audio_dev = (
            new.audio_dev_no_rds_hz
            if not new.rds_enabled and new.audio_dev_no_rds_hz
            else new.audio_dev_hz
        )
        tx.set_audio(deviation_hz=audio_dev, mute=False, preemph_us=new.preemph_us)
    if old.frequency_khz != new.frequency_khz:
        tx.set_frequency_10khz(new.freq_10khz)
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
    if old.rds_dev_hz != new.rds_dev_hz:
        tx.rds_set_deviation(new.rds_dev_hz)
    if old.rds_enabled != new.rds_enabled:
        tx.rds_enable(new.rds_enabled)
    # PS
    if old.rds_ps_center != new.rds_ps_center or old.rds_ps != new.rds_ps:
        macro_ctx = _macro_context(config_name)
        ps_slots, _rendered = _render_ps_slots(
            new.rds_ps, center=new.rds_ps_center, macro_ctx=macro_ctx
        )
        for text8, idx in ps_slots:
            tx.rds_set_ps(text8, idx)
        if status_bus is not None:
            status_bus.update_ps(new.rds_ps)
            if _rendered:
                status_bus.update_ps_current(_rendered[0].strip())
        logger.info("PS updated: %s", new.rds_ps)
    if old.rds_ps_count != new.rds_ps_count or old.rds_ps_speed != new.rds_ps_speed:
        tx.rds_set_pscount(max(1, new.rds_ps_count), max(1, new.rds_ps_speed))
    return rt_dep_changed


def recover_tx(tx: SI4713, cfg: AppConfig) -> bool:
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


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def load_yaml_config(path: str) -> AppConfig:
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
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return {}
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to read state %s: %s", path, exc)
        return {}


def save_state(path: str, data: Dict[str, Any]) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to write state %s: %s", path, exc)


def _first_config_from_dir(cfg_dir: str) -> Optional[str]:
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
    args = parser.parse_args()

    adapter_cfg = load_adapter_config(args.adapter_config)

    state = load_state(STATE_PATH)
    cfg_from_state = state.get("config_path") if isinstance(state, dict) else None

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
            from web import StatusBus, create_app, run_app

            logging.getLogger("werkzeug").setLevel(logging.WARNING)
        except Exception as exc:  # noqa: BLE001
            logger.error("API requested but Flask is not available: %s", exc)
        else:
            status_bus = StatusBus()
            status_bus.set_config_path(cfg_path)
            api_app = create_app(
                status_bus, os.path.dirname(os.path.abspath(cfg_path)), STATE_PATH
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
                },
            )

    tx = SI4713(
        i2c_bus=i2c_bus,
        backend=backend,
        ftdi_url=ftdi_url,
        ftdi_reset_pin=ftdi_reset_pin,
    )

    # Update state once we have successfully instantiated TX (even if API is off)
    if isinstance(state, dict):
        save_state(
            STATE_PATH,
            {
                "config_path": cfg_path,
                "api_enabled": api_enabled,
                "api_port": api_port_arg if api_port_arg else 0,
                "api_host": api_host_arg or "0.0.0.0",
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
    player_proc: Optional[subprocess.Popen[bytes]] = None
    last_player_cfg: Tuple[Optional[bool], Optional[str]] = (None, None)
    file_mtime: Optional[float] = _get_mtime(cfg.rds_rt_file)

    def refresh_player(current_cfg: AppConfig) -> None:
        nonlocal player_proc, last_player_cfg
        desired = (
            current_cfg.audio_play_enabled,
            current_cfg.audio_stream_url,
        )
        if desired == last_player_cfg:
            return
        _stop_player(player_proc)
        player_proc = None
        last_player_cfg = desired
        if not current_cfg.audio_play_enabled:
            logger.info("Audio player disabled")
            return
        if not current_cfg.audio_stream_url:
            logger.warning("Audio player enabled but no stream_url provided")
            return
        template = str(adapter_cfg.get("audio_player_cmd") or "").strip()
        if not template:
            logger.warning("Audio player command not configured")
            return
        device_flag_tpl = str(adapter_cfg.get("audio_player_device_flag") or "").strip()
        device = _parse_str(adapter_cfg.get("audio_device", "auto"), "auto") or "auto"
        url_arg_tpl = str(adapter_cfg.get("audio_player_url_arg") or "{url}")
        logger.info(
            "Starting audio player: url=%s device=%s",
            current_cfg.audio_stream_url,
            device,
        )
        try:
            # Quote device/URL so shlex.split keeps them together even when they contain spaces.
            url_safe = shlex.quote(current_cfg.audio_stream_url)
            device_safe = shlex.quote(device)
            cmd_str = template.format(url=url_safe, device=device_safe)
            cmd_parts = shlex.split(cmd_str)
            if not cmd_parts:
                raise ValueError("audio_player_cmd produced empty command")
            if device_flag_tpl:
                flag_str = device_flag_tpl.format(device=device_safe)
                tokens = shlex.split(flag_str)
                cmd_parts += tokens
            if url_arg_tpl:
                cmd_parts += shlex.split(url_arg_tpl.format(url=url_safe))
            logger.info("Audio player argv: %s", cmd_parts)
            player_proc = subprocess.Popen(
                cmd_parts, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            logger.info("Audio player started: %s", cmd_parts)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed to start audio player: %s (cmd=%r)",
                exc,
                cmd_parts if "cmd_parts" in locals() else cmd_str,
            )

    def apply_new_config(
        new_cfg_path: str,
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
            tx, new_cfg, cfg_nm, status_bus=status_bus
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
        ) = apply_config(tx, cfg, cfg_name, status_bus=status_bus)
        ps_macros_used = any(_has_macros(ps) for ps in cfg.rds_ps)
        next_ps_macro_refresh = (
            time.monotonic() + 60.0 if ps_macros_used else float("inf")
        )
        refresh_player(cfg)
        if status_bus is not None:
            status_bus.set_config_path(cfg_path)
        logger.info("Loaded config: %s", cfg_path)

        if cfg.monitor_health:
            if tx.is_transmitting():
                logger.info("TX is up at %.2f MHz", cfg.frequency_khz / 1000.0)
            else:
                logger.error("TX not running after setup")
                if not recover_tx(tx, cfg):
                    logger.critical("TX failed to start after recovery attempts")
                    tx.hw_reset(RESET_PIN)
                    sys.exit(1)
        else:
            logger.info("Health monitoring disabled; not verifying TX up/down")

        while True:
            # Config switch requested via API
            if status_bus is not None:
                pending_cfg = status_bus.pop_pending_config()
                if not pending_cfg:
                    current_from_api = status_bus.current_config_path()
                    if (
                        current_from_api
                        and os.path.abspath(current_from_api) != cfg_path
                    ):
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
                        ) = apply_new_config(pending_cfg)
                        ps_macros_used = any(_has_macros(ps) for ps in cfg.rds_ps)
                        next_ps_macro_refresh = (
                            time.monotonic() + 60.0 if ps_macros_used else float("inf")
                        )
                        refresh_player(cfg)
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
                            },
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.error("Failed to switch config %s: %s", pending_cfg, exc)
                else:
                    logger.debug("No pending config switch; current=%s", cfg_path)

            macro_ctx = _macro_context(cfg_name)

            # Health
            if cfg.monitor_health and not tx.is_transmitting():
                logger.error("TX dropped!")
                if not recover_tx(tx, cfg):
                    logger.critical("Unrecoverable TX failure; stopping")
                    tx.hw_reset(RESET_PIN)
                    sys.exit(2)

            # ASQ
            if cfg.monitor_asq:
                overmod, inlvl = tx.read_asq()
                if (
                    overmod
                    and cfg.monitor_overmod_ignore_dbfs is not None
                    and inlvl <= cfg.monitor_overmod_ignore_dbfs
                ):
                    overmod = False
                logger.info(
                    "Input Level: %d dBFS%s", inlvl, "   OVERMOD!!!" if overmod else ""
                )

            # PS macro refresh (e.g., time/date) once per minute
            if ps_macros_used and time.monotonic() >= next_ps_macro_refresh:
                ps_slots, rendered = _render_ps_slots(
                    cfg.rds_ps, center=cfg.rds_ps_center, macro_ctx=macro_ctx
                )
                for text8, slot in ps_slots:
                    tx.rds_set_ps(text8, slot)
                last_ps_render = rendered
                next_ps_macro_refresh = time.monotonic() + 60.0
                if status_bus is not None and rendered:
                    status_bus.update_ps_current(rendered[0].strip())

            # Config hot-reload
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
                        tx, cfg, new_cfg, cfg_name, status_bus
                    )
                    cfg = new_cfg
                    last_cfg_mtime = mtime
                    ps_macros_used = any(_has_macros(ps) for ps in cfg.rds_ps)
                    macro_ctx = _macro_context(cfg_name)
                    _slots, last_ps_render = _render_ps_slots(
                        cfg.rds_ps, center=cfg.rds_ps_center, macro_ctx=macro_ctx
                    )
                    next_ps_macro_refresh = (
                        time.monotonic() + 60.0 if ps_macros_used else float("inf")
                    )
                    refresh_player(cfg)

                    # Re-evaluate RT source & push if changed or deps changed
                    macro_ctx = _macro_context(cfg_name)
                    candidate_file = _resolve_file_rt(cfg, macro_ctx)
                    if candidate_file is not None:
                        candidate = candidate_file
                        new_src = "file"
                    else:
                        candidate = _resolve_rotation_rt(cfg, rot_idx, macro_ctx) or ""
                        new_src = f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"

                    if rt_dep_changed or candidate != last_rt or new_src != rt_source:
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

                    next_rotate_at = time.monotonic() + max(0.5, cfg.rds_rt_speed_s)
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed to reload config: %s", exc)

            # RT file watcher
            if cfg.rds_rt_file:
                current_mtime = _get_mtime(cfg.rds_rt_file)

                if current_mtime is not None and current_mtime != file_mtime:
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

                if current_mtime is None and file_mtime is not None:
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

            now = time.monotonic()
            # RT rotation tick (only when file is not active)
            if rt_source != "file" and cfg.rds_rt_texts and now >= next_rotate_at:
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

            # PS rotation display tick (UI only, SI4713 cycles internally)
            if cfg.rds_ps and len(cfg.rds_ps) > 1 and now >= next_ps_rotate:
                ps_idx = (ps_idx + 1) % len(cfg.rds_ps)
                if status_bus is not None:
                    status_bus.update_ps_current(cfg.rds_ps[ps_idx])
                logger.info("PS rotate -> list[%d]: %s", ps_idx, cfg.rds_ps[ps_idx])
                next_ps_rotate = now + max(0.5, cfg.rds_ps_speed)

            time.sleep(max(0.1, cfg.health_interval_s))

    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as exc:  # noqa: BLE001
        logger.error("Fatal error: %s", exc)
    finally:
        _stop_player(player_proc)
        try:
            tx.hw_reset(RESET_PIN)
        finally:
            tx.close()
        logger.info("Cleanup done, TX stopped")


if __name__ == "__main__":
    main()
