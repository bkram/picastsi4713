#!/usr/bin/env python3
"""
SI4713 FM+RDS transmitter

Usage:
    python3 run_tx.py --cfg station.yml
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
from si4713 import SI4713


try:
    import yaml  # type: ignore[import-not-found]
except Exception as exc:  # noqa: BLE001
    yaml = None  # type: ignore[assignment]
    _yaml_import_error = exc
else:
    _yaml_import_error = None

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

RESET_PIN: int = 5
REFCLK_HZ: int = 32768

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


def _ps_pairs(ps: List[str], center: bool) -> List[Tuple[str, int]]:
    pairs: List[Tuple[str, int]] = []
    for idx, item in enumerate(ps):
        text8 = _center_fixed(item or "", 8) if center else (item or "")[:8].ljust(8)
        pairs.append((text8, idx))
    return pairs


# ---------------------------------------------------------------------
# Config model
# ---------------------------------------------------------------------


class AppConfig:
    # RF
    frequency_khz: int
    power: int  # 88..120 dBµV
    antenna_cap: int

    # RDS flags
    rds_pi: int
    rds_pty: int
    rds_tp: bool
    rds_ta: bool
    rds_ms_music: bool
    di_stereo: bool
    di_artificial_head: bool
    di_compressed: bool
    di_dynamic_pty: bool

    # PS
    rds_ps: List[str]
    rds_ps_center: bool
    rds_ps_count: int
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
        self.antenna_cap = max(0, min(255, _parse_int(rf.get("antenna_cap", 4), 4)))

        # RDS flags
        _enforce("pi" in rds, "rds.pi is required")
        _enforce("pty" in rds, "rds.pty is required")
        _enforce("ps" in rds, "rds.ps is required")
        _enforce(
            isinstance(rds.get("ps"), list) and rds["ps"],
            "rds.ps must be a non-empty list",
        )

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
        ps_count_raw = rds.get("ps_count", None)
        self.rds_ps_count = (
            max(1, _parse_int(ps_count_raw, len(self.rds_ps)))
            if ps_count_raw is not None
            else max(1, len(self.rds_ps))
        )

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

    @property
    def freq_10khz(self) -> int:
        return int(round(self.frequency_khz / 10.0))


# ---------------------------------------------------------------------
# RT helpers
# ---------------------------------------------------------------------


def _fmt_rt(s: str, center: bool) -> str:
    return _center_fixed(s, 32) if center else s[:32]


def _resolve_file_rt(cfg: AppConfig) -> Optional[str]:
    if not cfg.rds_rt_file:
        return None
    mt = _get_mtime(cfg.rds_rt_file)
    if mt is None:
        return None
    raw = _read_text_file(cfg.rds_rt_file)
    if not raw:
        return None
    norm = _normalize_rt_source(raw)
    if any(sw in norm.lower() for sw in cfg.rds_rt_skip_words):
        return None
    return _fmt_rt(norm, cfg.rds_rt_center)


def _resolve_rotation_rt(cfg: AppConfig, idx: int) -> Optional[str]:
    if cfg.rds_rt_texts:
        return _fmt_rt(cfg.rds_rt_texts[idx % len(cfg.rds_rt_texts)], cfg.rds_rt_center)
    if cfg.rds_rt_text:
        return _fmt_rt(cfg.rds_rt_text, cfg.rds_rt_center)
    return None


def _burst_rt(
    tx: SI4713,
    text: str,
    *,
    ab_mode: str,
    repeats: int,
    gap_ms: int,
    bank: Optional[int],
) -> None:
    """
    UECP-like: first send may flip AB (auto) if content differs; then repeat same bank.
    """
    tx.set_rt_ab_mode(ab_mode)
    # First send (potential AB flip in 'auto')
    tx.rds_set_rt(text, bank=bank if ab_mode == "bank" else None)
    # More sends (same content => no AB flip in 'auto')
    for _ in range(max(0, repeats - 1)):
        time.sleep(gap_ms / 1000.0)
        tx.rds_set_rt(text, bank=bank if ab_mode == "bank" else None)


# ---------------------------------------------------------------------
# Apply + live reconfig + recover
# ---------------------------------------------------------------------


def apply_config(tx: SI4713, cfg: AppConfig) -> Tuple[str, str, int, float]:
    # RF / audio
    tx.set_output(cfg.power, cfg.antenna_cap)
    tx.set_frequency_10khz(cfg.freq_10khz)
    tx.enable_mpx(True)

    # Pilot/audio
    tx.set_pilot(freq_hz=19000, dev_hz=675)  # 6.75 kHz
    tx.set_audio(deviation_hz=7500, mute=False, preemph_us=50)  # 75.00 kHz, 50 µs

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
    for text8, slot in _ps_pairs(cfg.rds_ps, center=cfg.rds_ps_center):
        tx.rds_set_ps(text8, slot)
    tx.rds_set_pscount(max(1, cfg.rds_ps_count), max(1, cfg.rds_ps_speed))
    tx.rds_enable(True)

    # RT initial
    rt_text: Optional[str] = _resolve_file_rt(cfg)
    source: str
    rot_idx = 0
    now = time.monotonic()
    next_rotate_at = now + max(0.5, cfg.rds_rt_speed_s)

    if rt_text is not None:
        source = "file"
    else:
        rt_text = _resolve_rotation_rt(cfg, rot_idx) or ""
        source = f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"

    _burst_rt(
        tx,
        rt_text,
        ab_mode=cfg.rds_rt_ab_mode,
        repeats=cfg.rds_rt_repeats,
        gap_ms=cfg.rds_rt_gap_ms,
        bank=cfg.rds_rt_bank,
    )

    logger.info("RT source active: %s -> %r", source, rt_text)
    return rt_text, source, rot_idx, next_rotate_at


def reconfigure_live(tx: SI4713, old: AppConfig, new: AppConfig) -> bool:
    rt_dep_changed = False
    if old.power != new.power or old.antenna_cap != new.antenna_cap:
        tx.set_output(new.power, new.antenna_cap)
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
    # PS
    if old.rds_ps_center != new.rds_ps_center or old.rds_ps != new.rds_ps:
        for idx, text in enumerate(new.rds_ps):
            text8 = _center_fixed(text, 8) if new.rds_ps_center else text[:8].ljust(8)
            tx.rds_set_ps(text8, idx)
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
        time.sleep(0.1)
        if not tx.init(RESET_PIN, REFCLK_HZ):
            time.sleep(cfg.recovery_backoff_s * attempt)
            continue
        try:
            _rt, _src, _idx, _next = apply_config(tx, cfg)
        except Exception as exc:  # noqa: BLE001
            logger.error("Reconfigure failed: %s", exc)
            time.sleep(cfg.recovery_backoff_s * attempt)
            continue
        if tx.is_transmitting():
            logger.info("TX recovered on attempt %d", attempt)
            return True
        time.sleep(cfg.recovery_backoff_s * attempt)
    return False


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------


def load_yaml_config(path: str) -> AppConfig:
    if yaml is None:
        logger.critical("PyYAML is required: pip install pyyaml")
        raise SystemExit(2) from _yaml_import_error
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if not isinstance(raw, dict):
        logger.critical("Config root must be a mapping/dictionary")
        raise SystemExit(2)
    return AppConfig(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="SI4713 FM+RDS transmitter")
    parser.add_argument(
        "--cfg", type=str, required=True, help="Path to YAML configuration file"
    )
    args = parser.parse_args()

    cfg_path = args.cfg
    cfg = load_yaml_config(cfg_path)
    last_cfg_mtime = _get_mtime(cfg_path)

    tx = SI4713()

    last_rt: Optional[str]
    rt_source: str
    rot_idx: int
    next_rotate_at: float
    file_mtime: Optional[float] = _get_mtime(cfg.rds_rt_file)

    try:
        if not tx.init(RESET_PIN, REFCLK_HZ):
            logger.error("Init failed")
            tx.hw_reset(RESET_PIN)
            sys.exit(1)

        last_rt, rt_source, rot_idx, next_rotate_at = apply_config(tx, cfg)

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
                logger.info(
                    "Input Level: %d dBFS%s", inlvl, "   OVERMOD!!!" if overmod else ""
                )

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
                    rt_dep_changed = reconfigure_live(tx, cfg, new_cfg)
                    cfg = new_cfg
                    last_cfg_mtime = mtime

                    # Re-evaluate RT source & push if changed or deps changed
                    candidate_file = _resolve_file_rt(cfg)
                    if candidate_file is not None:
                        candidate = candidate_file
                        new_src = "file"
                    else:
                        candidate = _resolve_rotation_rt(cfg, rot_idx) or ""
                        new_src = f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"

                    if rt_dep_changed or candidate != last_rt or new_src != rt_source:
                        _burst_rt(
                            tx,
                            candidate,
                            ab_mode=cfg.rds_rt_ab_mode,
                            repeats=cfg.rds_rt_repeats,
                            gap_ms=cfg.rds_rt_gap_ms,
                            bank=cfg.rds_rt_bank,
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
                    candidate = _resolve_file_rt(cfg)
                    if candidate is not None:
                        if candidate != last_rt or rt_source != "file":
                            _burst_rt(
                                tx,
                                candidate,
                                ab_mode=cfg.rds_rt_ab_mode,
                                repeats=cfg.rds_rt_repeats,
                                gap_ms=cfg.rds_rt_gap_ms,
                                bank=cfg.rds_rt_bank,
                            )
                            logger.info("RT source switch: %s -> file", rt_source)
                            rt_source = "file"
                            last_rt = candidate
                        file_mtime = current_mtime
                    else:
                        alt = _resolve_rotation_rt(cfg, rot_idx) or ""
                        if alt != last_rt or rt_source == "file":
                            _burst_rt(
                                tx,
                                alt,
                                ab_mode=cfg.rds_rt_ab_mode,
                                repeats=cfg.rds_rt_repeats,
                                gap_ms=cfg.rds_rt_gap_ms,
                                bank=cfg.rds_rt_bank,
                            )
                            new_src = (
                                f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"
                            )
                            logger.info("RT source switch: file -> %s", new_src)
                            rt_source = new_src
                            last_rt = alt
                        file_mtime = current_mtime

                if current_mtime is None and file_mtime is not None:
                    alt = _resolve_rotation_rt(cfg, rot_idx) or ""
                    if alt != last_rt or rt_source == "file":
                        _burst_rt(
                            tx,
                            alt,
                            ab_mode=cfg.rds_rt_ab_mode,
                            repeats=cfg.rds_rt_repeats,
                            gap_ms=cfg.rds_rt_gap_ms,
                            bank=cfg.rds_rt_bank,
                        )
                        new_src = f"list[{rot_idx}]" if cfg.rds_rt_texts else "fallback"
                        logger.info("RT source switch: file -> %s", new_src)
                        rt_source = new_src
                        last_rt = alt
                    file_mtime = current_mtime

            # RT rotation tick (only when file is not active)
            now = time.monotonic()
            if rt_source != "file" and cfg.rds_rt_texts and now >= next_rotate_at:
                rot_idx = (rot_idx + 1) % len(cfg.rds_rt_texts)
                candidate = _resolve_rotation_rt(cfg, rot_idx) or ""
                if candidate != last_rt or not rt_source.startswith("list["):
                    _burst_rt(
                        tx,
                        candidate,
                        ab_mode=cfg.rds_rt_ab_mode,
                        repeats=cfg.rds_rt_repeats,
                        gap_ms=cfg.rds_rt_gap_ms,
                        bank=cfg.rds_rt_bank,
                    )
                    logger.info("RT rotate -> list[%d]: %r", rot_idx, candidate)
                    rt_source = f"list[{rot_idx}]"
                    last_rt = candidate
                next_rotate_at = now + max(0.5, cfg.rds_rt_speed_s)

            time.sleep(max(0.1, cfg.health_interval_s))

    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except Exception as exc:  # noqa: BLE001
        logger.error("Fatal error: %s", exc)
    finally:
        try:
            tx.hw_reset(RESET_PIN)
        finally:
            tx.close()
        logger.info("Cleanup done, TX stopped")


if __name__ == "__main__":
    main()
