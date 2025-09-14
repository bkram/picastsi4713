#!/usr/bin/env python3
"""
SI4713 FM Transmitter Control Library
Based on work by PE5PVB (https://github.com/PE5PVB/si4713)
"""

from __future__ import annotations

import logging
import threading
import time
from typing import List, Optional, Tuple

import RPi.GPIO as GPIO
import smbus2

logger = logging.getLogger(__name__)

I2C_ADDRESS: int = 0x63
I2C_BUS: int = 1


class SI4713:
    """Control class for SI4713 FM transmitter with RDS."""

    def __init__(self, i2c_addr: int = I2C_ADDRESS, i2c_bus: int = I2C_BUS) -> None:
        self.addr: int = i2c_addr
        self.bus: smbus2.SMBus = smbus2.SMBus(i2c_bus)
        self.lock: threading.Lock = threading.Lock()
        self.buf: List[int] = [0] * 10

        self.component: int = 0
        self.acomp: int = 0
        self.misc: int = 0

        self._rt_ab_mode: str = "auto"  # 'legacy' | 'auto' | 'bank'
        self._rt_ab: int = 1  # 0=A, 1=B
        self._last_rt: Optional[bytes] = None  # last 32-byte payload

    # ---------- Low-level helpers ----------

    def _write_buf(self, nbytes: int) -> bool:
        try:
            with self.lock:
                self.bus.write_i2c_block_data(
                    self.addr, self.buf[0], self.buf[1:nbytes]
                )
                time.sleep(0.054)
                for _ in range(10):
                    status = self.bus.read_byte(self.addr)
                    if status & 0x80:
                        return True
                    time.sleep(0.001)
                logger.error("CTS timeout after write")
                return False
        except Exception as exc:  # noqa: BLE001
            logger.error("I2C write error: %s", exc)
            return False

    def _set_prop(self, prop: int, val: int) -> bool:
        self.buf[0] = 0x12
        self.buf[1] = 0x00
        self.buf[2] = (prop >> 8) & 0xFF
        self.buf[3] = prop & 0xFF
        self.buf[4] = (val >> 8) & 0xFF
        self.buf[5] = val & 0xFF
        return self._write_buf(6)

    # ---------- Public control API ----------

    def init(self, rst_pin: int, refclk_hz: int) -> bool:
        try:
            GPIO.setwarnings(False)
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(rst_pin, GPIO.OUT)

            # HW reset: High→Low→High
            GPIO.output(rst_pin, GPIO.HIGH)
            time.sleep(0.05)
            GPIO.output(rst_pin, GPIO.LOW)
            time.sleep(0.05)
            GPIO.output(rst_pin, GPIO.HIGH)
            time.sleep(0.05)

            with self.lock:
                self.bus.write_i2c_block_data(
                    self.addr, 0x01, [0x12, 0x50])  # POWER_UP

            # Default GPO
            self.buf[0] = 0x80
            self.buf[1] = 0x0E
            self._write_buf(2)

            if not self._set_prop(0x0201, refclk_hz):  # REFCLK
                logger.error("Failed to set REFCLK")
                return False

            self._set_prop(0x2300, 0x0007)  # audio inputs mask (example)
            logger.info("SI4713 init OK")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Init error: %s", exc)
            return False

    def hw_reset(self, rst_pin: int) -> None:
        try:
            GPIO.output(rst_pin, GPIO.LOW)
            time.sleep(0.05)
            logger.info("Hardware reset asserted (TX stopped)")
        except Exception as exc:  # noqa: BLE001
            logger.error("HW reset failed: %s", exc)

    def set_frequency_10khz(self, f10k: int) -> None:
        self.buf[0] = 0x30
        self.buf[1] = 0x00
        self.buf[2] = (f10k >> 8) & 0xFF
        self.buf[3] = f10k & 0xFF
        self._write_buf(4)

    def set_output(self, level: int, cap: int) -> None:
        level = max(0, min(255, level))
        cap = max(0, min(255, cap))
        self.buf[0] = 0x31
        self.buf[1] = 0x00
        self.buf[2] = 0x00
        self.buf[3] = level
        self.buf[4] = cap
        self._write_buf(5)

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
                (self.misc | (1 << 12)) if dynamic_pty else (
                    self.misc & ~(1 << 12))
            )
        if compressed is not None:
            self.misc = (
                (self.misc | (1 << 13)) if compressed else (
                    self.misc & ~(1 << 13))
            )
        if artificial_head is not None:
            self.misc = (
                (self.misc | (1 << 14)) if artificial_head else (
                    self.misc & ~(1 << 14))
            )
        if stereo is not None:
            self.misc = (self.misc | (1 << 15)) if stereo else (
                self.misc & ~(1 << 15))
        self._set_prop(0x2C03, self.misc)

    def rds_set_deviation(self, dev_10hz: int) -> None:
        self._set_prop(0x2103, dev_10hz)

    def rds_set_af(self, af_code: int) -> None:
        if af_code == 0:
            self._set_prop(0x2C06, 0xE0E0)
        else:
            self._set_prop(0x2C06, 0xDD95 + af_code)

    def rds_set_ps(self, text: str, slot: int) -> None:
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

    def rds_set_rt(self, text: str, bank: Optional[int] = None) -> None:
        """
        Send RadioText using Group 2A (32 chars) with UECP-like A/B rules.

        - 'legacy': always bank A.
        - 'auto': flip A/B if 32-byte payload differs from last.
        - 'bank': use provided bank (0=A, 1=B); if None, reuse last.

        Args:
            text: RT string (first 32 characters are used; padded to 32).
            bank: Optional explicit bank for 'bank' mode.
        """
        # Build fixed 32-char payload
        arr = [" "] * 32
        for i in range(min(32, len(text))):
            arr[i] = text[i]
        payload = "".join(arr).encode("latin-1", "replace")

        # Decide bank per mode
        mode = self._rt_ab_mode
        if mode == "legacy":
            bank_to_send = 0
        elif mode == "bank":
            if bank is not None:
                self._rt_ab = int(bank) & 1
            bank_to_send = self._rt_ab & 1
        else:  # 'auto' (default)
            if self._last_rt != payload:
                self._rt_ab ^= 1
            bank_to_send = self._rt_ab & 1

        # Prepare fields used in Block B
        tp = (self.misc >> 10) & 0x01
        pty = (self.misc >> 5) & 0x1F
        ab = bank_to_send & 0x01

        # Write 8 segments of 4 chars
        idx = 0
        for seg in range(8):
            block_b = (
                (2 << 12)
                | (0 << 11)
                | (tp << 10)
                | (pty << 5)
                | (ab << 4)
                | (seg & 0x0F)
            )
            self.buf[0] = 0x35
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

    # ---------- Status / health ----------

    def tx_status(self) -> Optional[Tuple[int, int, bool, int]]:
        try:
            self.buf[0] = 0x33
            self.buf[1] = 0x00
            if not self._write_buf(2):
                return None
            with self.lock:
                resp = self.bus.read_i2c_block_data(self.addr, 0, 8)
            freq_10khz = (resp[2] << 8) | resp[3]
            power_level = resp[5]
            overmod = bool(resp[1] & 0x04)
            return (freq_10khz, power_level, overmod, 0)
        except Exception as exc:  # noqa: BLE001
            logger.error("tx_status failed: %s", exc)
            return None

    def is_transmitting(self) -> bool:
        st = self.tx_status()
        if st is None:
            return False
        _, pwr, _, _ = st
        return pwr > 0

    def read_asq(self) -> Tuple[bool, int]:
        try:
            self.buf[0] = 0x34
            self.buf[1] = 0x00
            self._write_buf(2)
            with self.lock:
                resp = self.bus.read_i2c_block_data(self.addr, 0, 5)
            overmod = bool(resp[1] & 0x04)
            inlevel = resp[4] if resp[4] < 128 else resp[4] - 256
            self.buf[0] = 0x34
            self.buf[1] = 0x01
            self._write_buf(2)
            return overmod, inlevel
        except Exception as exc:  # noqa: BLE001
            logger.error("ASQ read error: %s", exc)
            return False, 0

    def read_revision(self) -> Tuple[int, int]:
        try:
            self.buf[0] = 0x10
            self._write_buf(1)
            with self.lock:
                resp = self.bus.read_i2c_block_data(self.addr, 0, 9)
            return resp[1], resp[8]
        except Exception as exc:  # noqa: BLE001
            logger.error("Revision read error: %s", exc)
            return 0, 0

    def close(self) -> None:
        try:
            GPIO.cleanup()
        except Exception:
            pass
        try:
            self.bus.close()
        except Exception:
            pass
