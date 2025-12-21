#!/usr/bin/env python3
"""
SI4713 FM Transmitter Control Library
Based on work by PE5PVB (https://github.com/PE5PVB/si4713)
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, List, Optional, Tuple

# RPi backend (default on Raspberry Pi)
try:
    import RPi.GPIO as GPIO  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    GPIO = None  # type: ignore[assignment]

try:
    import smbus2  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    smbus2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

I2C_ADDRESS: int = 0x63
I2C_BUS: int = 1


# ---------------------------------------------------------------------
# FT232H helpers
# ---------------------------------------------------------------------


class _Ft232hBus:
    """Minimal SMBus-like wrapper around pyftdi's I2C port."""

    def __init__(self, port: "pyftdi.i2c.I2cPort") -> None:  # type: ignore[name-defined]
        self._port = port

    def write_i2c_block_data(
        self, addr: int, cmd: int, data: List[int]
    ) -> None:  # noqa: ARG002
        payload = bytes([cmd, *data])
        self._port.write(payload)

    def read_byte(self, addr: int) -> int:  # noqa: ARG002
        return int(self._port.read(1)[0])

    def read_i2c_block_data(
        self, addr: int, cmd: int, length: int
    ) -> List[int]:  # noqa: ARG002
        self._port.write(bytes([cmd]))
        data = self._port.read(length)
        return list(data)

    def close(self) -> None:
        """Match smbus API; controller owns the port."""
        return None


class _Ft232hGpio:
    """Shim that mimics the tiny subset of RPi.GPIO we use for reset."""

    BCM = 1
    OUT = 1
    HIGH = 1
    LOW = 0

    def __init__(self, ctrl: "pyftdi.i2c.I2cController", pin: int) -> None:  # type: ignore[name-defined]
        self._pin = pin
        self._mask = 1 << pin
        self._gpio = ctrl.get_gpio()
        self._gpio.set_direction(
            self._mask, self._mask
        )  # configure reset pin as output
        self._state = 0

    def setwarnings(self, flag: bool) -> None:  # noqa: ARG002
        return None

    def setmode(self, mode: int) -> None:  # noqa: ARG002
        return None

    def setup(self, pin: int, mode: int) -> None:  # noqa: ARG002
        if pin != self._pin:
            raise ValueError(
                f"FT232H reset pin mismatch: expected {self._pin}, got {pin}"
            )

    def output(self, pin: int, value: int) -> None:
        if pin != self._pin:
            raise ValueError(
                f"FT232H reset pin mismatch: expected {self._pin}, got {pin}"
            )
        if value:
            self._state |= self._mask
        else:
            self._state &= ~self._mask
        self._gpio.write(self._state)

    def cleanup(self) -> None:
        try:
            self._gpio.write(self._state & ~self._mask)
        except Exception:
            pass


class _Ft232hBackend:
    """Bundle FT232H I2C + GPIO helpers."""

    def __init__(self, url: str, reset_pin: int, addr: int) -> None:
        try:
            from pyftdi.i2c import I2cController  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise ImportError(
                "pyftdi is required for the FT232H backend (pip install pyftdi)"
            ) from exc

        self.ctrl = I2cController()
        self.ctrl.configure(url)
        self.port = self.ctrl.get_port(addr)
        self.bus = _Ft232hBus(self.port)
        self.gpio = _Ft232hGpio(self.ctrl, reset_pin)

    def close(self) -> None:
        try:
            self.ctrl.terminate()
        except Exception:
            return None


# ---------------------------------------------------------------------
# Blinka FT232H helpers (board/busio/digitalio)
# ---------------------------------------------------------------------


class _BlinkaBus:
    """SMBus-like shim using Adafruit Blinka busio.I2C."""

    def __init__(self, i2c: "busio.I2C") -> None:  # type: ignore[name-defined]
        self._i2c = i2c
        self._locked = False
        self._ensure_lock()

    def _ensure_lock(self) -> None:
        if self._locked:
            return
        # Try to lock the bus; sleep briefly between attempts.
        for _ in range(1000):
            if self._i2c.try_lock():
                self._locked = True
                return
            time.sleep(0.001)
        raise RuntimeError("Failed to lock I2C bus (Blinka)")

    def write_i2c_block_data(self, addr: int, cmd: int, data: List[int]) -> None:
        self._ensure_lock()
        self._i2c.writeto(addr, bytes([cmd, *data]))

    def read_byte(self, addr: int) -> int:
        self._ensure_lock()
        buf = bytearray(1)
        self._i2c.readfrom_into(addr, buf)
        return int(buf[0])

    def read_i2c_block_data(self, addr: int, cmd: int, length: int) -> List[int]:
        self._ensure_lock()
        buf = bytearray(length)
        self._i2c.writeto_then_readfrom(addr, bytes([cmd]), buf)
        return list(buf)

    def close(self) -> None:
        try:
            if self._locked:
                self._i2c.unlock()
        finally:
            self._locked = False
            if hasattr(self._i2c, "deinit"):
                try:
                    self._i2c.deinit()  # type: ignore[attr-defined]
                except Exception:
                    pass


class _BlinkaGpio:
    """GPIO shim using Blinka digitalio for the reset pin only."""

    BCM = 1
    OUT = 1
    HIGH = 1
    LOW = 0

    def __init__(self, pin: "digitalio.DigitalInOut") -> None:  # type: ignore[name-defined]
        from digitalio import Direction  # type: ignore import  # noqa: WPS433

        self._pin = pin
        self._pin.direction = Direction.OUTPUT

    def setwarnings(self, flag: bool) -> None:  # noqa: ARG002
        return None

    def setmode(self, mode: int) -> None:  # noqa: ARG002
        return None

    def setup(self, pin: int, mode: int) -> None:  # noqa: ARG002
        return None

    def output(self, pin: int, value: int) -> None:  # noqa: ARG002
        self._pin.value = bool(value)

    def cleanup(self) -> None:
        try:
            if hasattr(self._pin, "deinit"):
                self._pin.deinit()
        except Exception:
            pass


class _BlinkaBackend:
    """Bundle Blinka I2C + GPIO helpers (for FT232H with BLINKA_FT232H=1)."""

    def __init__(self, reset_pin: int) -> None:
        try:
            import board  # type: ignore[import-not-found]
            import busio  # type: ignore[import-not-found]
            import digitalio  # type: ignore[import-not-found]
        except Exception as exc:  # noqa: BLE001
            raise ImportError(
                "Adafruit Blinka is required for the 'ft232h_blinka' backend "
                "(pip install adafruit-blinka)"
            ) from exc

        i2c = busio.I2C(board.SCL, board.SDA)
        pin_name = f"D{reset_pin}"
        try:
            reset_pin_obj = getattr(board, pin_name)
        except AttributeError as exc:  # noqa: BLE001
            raise ValueError(f"Blinka reset pin not found: {pin_name}") from exc

        self.bus = _BlinkaBus(i2c)
        self.gpio = _BlinkaGpio(digitalio.DigitalInOut(reset_pin_obj))

    def close(self) -> None:
        try:
            self.bus.close()
        finally:
            try:
                self.gpio.cleanup()
            except Exception:
                pass


class SI4713:
    """Control class for SI4713 FM transmitter with RDS."""

    def __init__(
        self,
        i2c_addr: int = I2C_ADDRESS,
        i2c_bus: int = I2C_BUS,
        backend: str = "auto",
        ftdi_url: Optional[str] = None,
        ftdi_reset_pin: int = 5,
    ) -> None:
        backend = (backend or "auto").lower()
        self.addr: int = i2c_addr
        self.backend: str = backend
        self._stop_event: Optional[threading.Event] = None

        self._ftdi_backend: Optional[_Ft232hBackend] = None
        self._blinka_backend: Optional[_BlinkaBackend] = None
        self.bus: Any = None
        self.gpio: Any = None

        use_rpi = backend in {"auto", "rpi"} and GPIO is not None and smbus2 is not None
        if use_rpi:
            self.bus = smbus2.SMBus(i2c_bus)  # type: ignore[assignment]
            self.gpio = GPIO  # type: ignore[assignment]
            self._close_bus = self.bus.close
            self._cleanup_gpio = getattr(self.gpio, "cleanup", lambda: None)
            logger.info("SI4713 backend: RPi/smbus2 (bus=%d)", i2c_bus)
        else:
            # Prefer Blinka when explicitly requested or when BLINKA_FT232H is set.
            want_blinka = backend in {"blinka", "ft232h_blinka"} or (
                backend == "auto" and os.getenv("BLINKA_FT232H") == "1"
            )
            if want_blinka:
                self._blinka_backend = _BlinkaBackend(ftdi_reset_pin)
                self.bus = self._blinka_backend.bus
                self.gpio = self._blinka_backend.gpio
                self._close_bus = getattr(self.bus, "close", lambda: None)
                self._cleanup_gpio = getattr(self.gpio, "cleanup", lambda: None)
                logger.info(
                    "SI4713 backend: Blinka FT232H (reset pin=D%d)", ftdi_reset_pin
                )
            else:
                url = ftdi_url or os.getenv("SI4713_FT232H_URL", "ftdi://ftdi:232h/1")
                reset_pin = int(
                    os.getenv("SI4713_FT232H_RESET_PIN", str(ftdi_reset_pin))
                )
                try:
                    self._ftdi_backend = _Ft232hBackend(url, reset_pin, i2c_addr)
                except ImportError as exc:
                    if backend in {"auto", "ft232h"} and GPIO is not None:
                        if smbus2 is None:
                            raise ImportError(
                                "pyftdi missing and smbus2 not available; install smbus2 "
                                "or set adapter to ft232h with pyftdi installed"
                            ) from exc
                        logger.warning(
                            "pyftdi missing; falling back to RPi backend (bus=%d). "
                            "Set adapter=rpi/auto for Pi or install pyftdi for FT232H.",
                            i2c_bus,
                        )
                        self.bus = smbus2.SMBus(i2c_bus)  # type: ignore[assignment]
                        self.gpio = GPIO  # type: ignore[assignment]
                        self._close_bus = self.bus.close
                        self._cleanup_gpio = getattr(self.gpio, "cleanup", lambda: None)
                        logger.info("SI4713 backend: RPi/smbus2 (fallback)")
                    else:
                        raise
                else:
                    self.bus = self._ftdi_backend.bus
                    self.gpio = self._ftdi_backend.gpio
                    self._close_bus = getattr(self.bus, "close", lambda: None)
                    self._cleanup_gpio = getattr(self.gpio, "cleanup", lambda: None)
                    logger.info(
                        "SI4713 backend: FT232H (%s), reset pin=%d", url, reset_pin
                    )

        self.lock: threading.Lock = threading.Lock()
        self.buf: List[int] = [0] * 10

        self.component: int = 0
        self.acomp: int = 0
        self.misc: int = 0

        self._prop_cache: dict[int, int] = {}
        self._last_freq_10khz: Optional[int] = None
        self._last_output: Optional[Tuple[int, int]] = None
        self._last_ps: dict[int, str] = {}
        self._rt_ab_mode: str = "auto"  # 'legacy' | 'auto' | 'bank'
        self._rt_ab: int = 1  # 0=A, 1=B
        self._last_rt: Optional[bytes] = None  # last 32-byte payload
        self._last_rt_bank: Optional[int] = None

    def set_stop_event(self, event: Optional[threading.Event]) -> None:
        self._stop_event = event

    def _should_stop(self) -> bool:
        return bool(self._stop_event and self._stop_event.is_set())

    def init(self, rst_pin: int, refclk_hz: int) -> bool:
        try:
            self._prop_cache.clear()
            self._last_freq_10khz = None
            self._last_output = None
            self._last_ps.clear()
            self._last_rt = None
            self._last_rt_bank = None
            # --- Safety: ensure the lock exists even if something odd happened
            if "lock" not in self.__dict__ or self.lock is None:
                import threading

                self.lock = threading.Lock()

            self.gpio.setwarnings(False)
            self.gpio.setmode(self.gpio.BCM)
            self.gpio.setup(rst_pin, self.gpio.OUT)

            # HW reset: High → Low → High (keep your original timings)
            self.gpio.output(rst_pin, self.gpio.HIGH)
            time.sleep(0.05)
            self.gpio.output(rst_pin, self.gpio.LOW)
            time.sleep(0.05)
            self.gpio.output(rst_pin, self.gpio.HIGH)
            time.sleep(0.05)

            with self.lock:
                self.bus.write_i2c_block_data(self.addr, 0x01, [0x12, 0x50])

            self.buf[0] = 0x80
            self.buf[1] = 0x0E
            if not self._write_buf(2):
                logger.error("GPO_CTL write failed")
                return False

            if not self._set_prop(0x0201, refclk_hz):
                logger.error("Failed to set REFCLK")
                return False

            self._set_prop(0x2300, 0x0007)

            self._last_rt = None

            logger.info("SI4713 init OK")
            return True

        except Exception as exc:  # noqa: BLE001
            logger.error("Init error: %s", exc)
            return False

    # ---------- Low-level helpers ----------

    def _write_buf(self, nbytes: int) -> bool:
        retries = 3
        for attempt in range(1, retries + 1):
            if self._should_stop():
                return False
            try:
                with self.lock:
                    self.bus.write_i2c_block_data(
                        self.addr, self.buf[0], self.buf[1:nbytes]
                    )
                    time.sleep(0.06)
                    for _ in range(50):
                        if self._should_stop():
                            return False
                        status = self.bus.read_byte(self.addr)
                        if status & 0x80:
                            return True
                        time.sleep(0.002)
                    logger.error("CTS timeout after write")
                    return False
            except Exception as exc:  # noqa: BLE001
                if self._should_stop():
                    return False
                logger.error(
                    "I2C write error (attempt %d/%d): %s", attempt, retries, exc
                )
                time.sleep(0.01 * attempt)
        return False

    def _set_prop(self, prop: int, val: int) -> bool:
        cached = self._prop_cache.get(prop)
        if cached is not None and cached == val:
            return True
        self.buf[0] = 0x12
        self.buf[1] = 0x00
        self.buf[2] = (prop >> 8) & 0xFF
        self.buf[3] = prop & 0xFF
        self.buf[4] = (val >> 8) & 0xFF
        self.buf[5] = val & 0xFF
        ok = self._write_buf(6)
        if ok:
            self._prop_cache[prop] = val
        return ok

    # ---------- Public control API ----------

    def hw_reset(self, rst_pin: int) -> None:
        try:
            self.gpio.output(rst_pin, self.gpio.LOW)
            time.sleep(0.05)
            logger.info("Hardware reset asserted (TX stopped)")
        except Exception as exc:  # noqa: BLE001
            logger.error("HW reset failed: %s", exc)
        finally:
            # >>> NEW: clear RT state so first new RT is guaranteed to be a "new message"
            self._last_rt = None
            self._last_rt_bank = None
            self._prop_cache.clear()
            self._last_freq_10khz = None
            self._last_output = None
            self._last_ps.clear()

    def set_frequency_10khz(self, f10k: int) -> None:
        if self._last_freq_10khz == f10k:
            return
        self.buf[0] = 0x30
        self.buf[1] = 0x00
        self.buf[2] = (f10k >> 8) & 0xFF
        self.buf[3] = f10k & 0xFF
        if not self._write_buf(4):
            if self._should_stop():
                return
            time.sleep(0.01)
            if not self._write_buf(4):
                return
        self._last_freq_10khz = f10k

    def set_output(self, level: int, cap: int) -> None:
        level = max(0, min(255, level))
        cap = max(0, min(255, cap))
        if self._last_output == (level, cap):
            return
        self.buf[0] = 0x31
        self.buf[1] = 0x00
        self.buf[2] = 0x00
        self.buf[3] = level
        self.buf[4] = cap
        if not self._write_buf(5):
            if self._should_stop():
                return
            time.sleep(0.01)
            if not self._write_buf(5):
                return
        self._last_output = (level, cap)

    def enable_mpx(self, on: bool) -> None:
        if on:
            self.component |= 0x03
        else:
            self.component &= ~0x03
        self._set_prop(0x2100, self.component)

    def set_pilot(self, freq_hz: int, dev_hz: int) -> None:
        self._set_prop(0x2107, freq_hz)
        self._set_prop(0x2102, dev_hz)

    def set_audio(self, deviation_hz: int, mute: bool, preemph_us: int) -> None:
        self._set_prop(0x2101, deviation_hz)
        self._set_prop(0x2105, 0x0003 if mute else 0x0000)
        if preemph_us == 0:
            self._set_prop(0x2106, 0x0002)
        elif preemph_us == 75:
            self._set_prop(0x2106, 0x0000)
        else:
            self._set_prop(0x2106, 0x0001)  # 50 us

    def set_audio_processing(
        self,
        agc_on: bool,
        limiter_on: bool,
        comp_thr: int,
        comp_att: int,
        comp_rel: int,
        comp_gain: int,
        lim_rel: int,
    ) -> None:
        if agc_on:
            self.acomp |= 1
        else:
            self.acomp &= ~1
        if limiter_on:
            self.acomp |= 1 << 1
        else:
            self.acomp &= ~(1 << 1)
        self._set_prop(0x2200, self.acomp)
        self._set_prop(0x2201, comp_thr & 0xFFFF)
        self._set_prop(0x2202, comp_att)
        self._set_prop(0x2203, comp_rel)
        self._set_prop(0x2204, comp_gain)
        self._set_prop(0x2205, lim_rel)

    # ---------- RDS controls ----------

    def rds_enable(self, on: bool) -> None:
        if on:
            self.component |= 1 << 2
        else:
            self.component &= ~(1 << 2)
        self._set_prop(0x2100, self.component)

    def rds_set_pi(self, pi: int) -> None:
        self._set_prop(0x2C01, pi)

    def rds_set_pty(self, pty: int) -> None:
        self.misc = (self.misc & 0xFC1F) | ((pty & 0x1F) << 5)
        self._set_prop(0x2C03, self.misc)

    def rds_set_tp(self, on: bool) -> None:
        if on:
            self.misc |= 1 << 10
        else:
            self.misc &= ~(1 << 10)
        self._set_prop(0x2C03, self.misc)

    def rds_set_ta(self, on: bool) -> None:
        if on:
            self.misc |= 1 << 4
        else:
            self.misc &= ~(1 << 4)
        self._set_prop(0x2C03, self.misc)

    def rds_set_ms_music(self, on: bool) -> None:
        if on:
            self.misc |= 1 << 3
        else:
            self.misc &= ~(1 << 3)
        self._set_prop(0x2C03, self.misc)

    def rds_set_di(
        self,
        stereo: Optional[bool] = None,
        artificial_head: Optional[bool] = None,
        compressed: Optional[bool] = None,
        dynamic_pty: Optional[bool] = None,
    ) -> None:
        if dynamic_pty is not None:
            self.misc = (
                (self.misc | (1 << 12)) if dynamic_pty else (self.misc & ~(1 << 12))
            )
        if compressed is not None:
            self.misc = (
                (self.misc | (1 << 13)) if compressed else (self.misc & ~(1 << 13))
            )
        if artificial_head is not None:
            self.misc = (
                (self.misc | (1 << 14)) if artificial_head else (self.misc & ~(1 << 14))
            )
        if stereo is not None:
            self.misc = (self.misc | (1 << 15)) if stereo else (self.misc & ~(1 << 15))
        self._set_prop(0x2C03, self.misc)

    def rds_set_deviation(self, dev_10hz: int) -> None:
        self._set_prop(0x2103, dev_10hz)

    def rds_set_af(self, af_code: int) -> None:
        if af_code == 0:
            self._set_prop(0x2C06, 0xE0E0)
        else:
            self._set_prop(0x2C06, 0xDD95 + af_code)

    def rds_set_ps(self, text: str, slot: int) -> None:
        prev = self._last_ps.get(slot)
        if prev == text:
            return
        arr = [" "] * 8
        for i in range(min(8, len(text))):
            arr[i] = text[i]
        group = slot * 2

        self.buf[0] = 0x36
        self.buf[1] = group
        self.buf[2:6] = list(map(ord, arr[0:4]))
        self._write_buf(6)

        self.buf[0] = 0x36
        self.buf[1] = group + 1
        self.buf[2:6] = list(map(ord, arr[4:8]))
        self._write_buf(6)
        self._last_ps[slot] = text

    def rds_set_pscount(self, count: int, speed: int) -> None:
        self._set_prop(0x2C05, count)
        self._set_prop(0x2C04, speed)

    def set_rt_ab_mode(self, mode: str) -> None:
        """
        Set RT A/B behaviour: 'legacy' | 'auto' | 'bank'.
        """
        m = (mode or "").strip().lower()
        if m not in ("legacy", "auto", "bank"):
            raise ValueError("rt_ab_mode must be 'legacy', 'auto', or 'bank'")
        self._rt_ab_mode = m

    def rds_set_rt(
        self,
        text: str,
        bank: Optional[int] = None,
        *,
        force_new_message: bool = False,
        cr_terminate: bool = True,
    ) -> int:
        """
        Send RadioText using Group 2A (32 chars here) with UECP-like A/B rules.

        Modes:
        - 'legacy': always bank A.
        - 'auto'  : flip A/B if payload differs OR force_new_message=True.
        - 'bank'  : use provided bank (0=A, 1=B); if None, reuse last.

        Args:
            text: RT string (first 32 chars used).
            bank: explicit bank for 'bank' mode.
            force_new_message: force A/B flip even if payload is identical.
            cr_terminate: insert 0x0D if shorter than full length (spec hint).
        """
        # ---- Build fixed 32-char payload with optional CR termination
        arr = [" "] * 32
        ln = min(32, len(text))
        for i in range(ln):
            arr[i] = text[i]
        if cr_terminate and ln < 32:
            # put a single CR at the first free position (if not already CR)
            if ln == 0 or arr[ln - 1] != "\r":
                arr[ln] = "\r"

        payload = "".join(arr).encode("latin-1", "replace")

        # ---- Decide bank per mode
        mode = self._rt_ab_mode
        if mode == "legacy":
            bank_to_send = 0
        elif mode == "bank":
            if bank is not None:
                self._rt_ab = int(bank) & 1
            bank_to_send = self._rt_ab & 1
        else:  # 'auto' (default)
            if force_new_message or self._last_rt != payload:
                self._rt_ab ^= 1
            bank_to_send = self._rt_ab & 1

        if (
            not force_new_message
            and self._last_rt == payload
            and self._last_rt_bank == bank_to_send
        ):
            return bank_to_send

        # ---- Prepare fields used in Block B (type 2A, version A)
        tp = (self.misc >> 10) & 0x01
        pty = (self.misc >> 5) & 0x1F
        ab = bank_to_send & 0x01

        # ---- Write 8 segments (0..7) of 4 chars = 32 chars total (type 2A)
        # A new text starts at segment 0; we always send a complete set, then repeat
        # (reliability per spec: send at least twice overall). :contentReference[oaicite:2]{index=2}
        idx = 0
        for seg in range(8):
            block_b = (
                (2 << 12)  # group type code = 2
                | (0 << 11)  # version A
                | (tp << 10)
                | (pty << 5)
                | (ab << 4)  # Text A/B flag
                | (seg & 0x0F)  # segment address
            )
            self.buf[0] = 0x35
            # reset/load first, then continue
            self.buf[1] = 0x06 if seg == 0 else 0x04
            self.buf[2] = (block_b >> 8) & 0xFF
            self.buf[3] = block_b & 0xFF
            self.buf[4] = ord(arr[idx])
            self.buf[5] = ord(arr[idx + 1])
            self.buf[6] = ord(arr[idx + 2])
            self.buf[7] = ord(arr[idx + 3])
            self._write_buf(8)
            idx += 4

        self._last_rt = payload
        self._last_rt_bank = bank_to_send
        return bank_to_send

    # ---------- Status / health ----------

    def tx_status(self) -> Optional[Tuple[int, int, bool, int]]:
        try:
            if self._should_stop():
                return None
            self.buf[0] = 0x33
            self.buf[1] = 0x00
            if not self._write_buf(2):
                return None
            with self.lock:
                resp = self.bus.read_i2c_block_data(self.addr, 0, 8)
            freq_10khz = (resp[2] << 8) | resp[3]
            power_level = resp[5]
            antcap = resp[6] if len(resp) > 6 else 0
            overmod = bool(resp[1] & 0x04)
            return (freq_10khz, power_level, overmod, antcap)
        except Exception as exc:  # noqa: BLE001
            logger.error("tx_status failed: %s", exc)
            return None

    def read_antenna_cap(self) -> Optional[int]:
        """Return the last reported antenna capacitance (0-191) if available."""
        st = self.tx_status()
        if st is None:
            return None
        return st[3]

    def is_transmitting(self) -> bool:
        st = self.tx_status()
        if st is None:
            return False
        _, pwr, _, _ = st
        return pwr > 0

    def read_asq(self) -> Tuple[bool, int]:
        try:
            if self._should_stop():
                return False, 0
            self.buf[0] = 0x34
            self.buf[1] = 0x00
            if not self._write_buf(2):
                return False, 0
            with self.lock:
                resp = self.bus.read_i2c_block_data(self.addr, 0, 5)
            overmod = bool(resp[1] & 0x04)
            inlevel = resp[4] if resp[4] < 128 else resp[4] - 256
            self.buf[0] = 0x34
            self.buf[1] = 0x01
            if not self._write_buf(2):
                return overmod, inlevel
            return overmod, inlevel
        except Exception as exc:  # noqa: BLE001
            logger.error("ASQ read error: %s", exc)
            return False, 0

    def read_revision(self) -> Tuple[int, int]:
        try:
            if self._should_stop():
                return 0, 0
            self.buf[0] = 0x10
            if not self._write_buf(1):
                return 0, 0
            with self.lock:
                resp = self.bus.read_i2c_block_data(self.addr, 0, 9)
            return resp[1], resp[8]
        except Exception as exc:  # noqa: BLE001
            logger.error("Revision read error: %s", exc)
            return 0, 0

    def close(self) -> None:
        try:
            self._close_bus()
        except Exception:
            pass
        try:
            self._cleanup_gpio()
        except Exception:
            pass
