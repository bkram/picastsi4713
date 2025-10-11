import threading
import time
from pathlib import Path
from typing import Any, Dict

import pytest

pytest.importorskip("yaml")

from webapp.transmitter import TransmitterManager, ValidationError


SAMPLE_CFG = """
rf:
  frequency_khz: 98700
  power: 115
  antenna_cap: 4
audio:
  stereo: true
  agc_on: false
  limiter_on: true
  comp_thr: -26
  comp_att: 1
  comp_rel: 4
  comp_gain: 12
  lim_rel: 60
  preemphasis_us: 50
rds:
  enabled: true
  pi: 0x1234
  pty: 5
  tp: true
  ta: false
  ms_music: true
  ps:
    - TESTFM
  ps_center: true
  ps_speed: 8
  ps_count: 1
  di:
    stereo: true
    artificial_head: false
    compressed: false
    dynamic_pty: false
  rt:
    text: "Welcome to PiCast"
    texts:
      - "Enjoy the music"
    speed_s: 12
    center: true
    file_path: ""
    skip_words: ["advert"]
    ab_mode: auto
    repeats: 2
    gap_ms: 80
monitor:
  health: false
  asq: false
  interval_s: 0.1
  recovery_attempts: 1
  recovery_backoff_s: 0.2
"""

ROTATING_CFG = (
    SAMPLE_CFG.replace("  ps:\n    - TESTFM\n", "  ps:\n    - FIRSTFM\n    - SECONDFM\n")
    .replace("  ps_speed: 8", "  ps_speed: 1")
    .replace("  ps_count: 1", "  ps_count: 2")
)


class StubSI4713:
    def __init__(self) -> None:
        self._transmitting = False
        self.freq_10khz = 0
        self.power = 0
        self.antenna = 0
        self.closed = False
        self.audio_settings: Dict[str, Any] = {}
        self.stereo_mode = True
        self.rds_enabled = True
        self.preemphasis_us = 50

    def init(self, *_: object, **__: object) -> bool:
        return True

    def set_output(self, power: int, antenna: int) -> None:
        self.power = power
        self.antenna = antenna

    def set_frequency_10khz(self, freq: int) -> None:
        self.freq_10khz = freq

    def enable_mpx(self, on: bool) -> None:
        self._transmitting = on

    def set_stereo_mode(self, stereo: bool) -> None:
        self.stereo_mode = stereo

    def set_pilot(self, *_: object, **__: object) -> None:
        return

    def set_audio(
        self,
        *,
        deviation_hz: int,
        mute: bool,
        preemph_us: int,
    ) -> None:
        self.audio_settings["deviation_hz"] = deviation_hz
        self.audio_settings["mute"] = mute
        self.preemphasis_us = preemph_us

    def set_audio_processing(
        self,
        *,
        agc_on: bool,
        limiter_on: bool,
        comp_thr: int,
        comp_att: int,
        comp_rel: int,
        comp_gain: int,
        lim_rel: int,
    ) -> None:
        self.audio_settings = {
            "agc_on": agc_on,
            "limiter_on": limiter_on,
            "comp_thr": comp_thr,
            "comp_att": comp_att,
            "comp_rel": comp_rel,
            "comp_gain": comp_gain,
            "lim_rel": lim_rel,
        }

    def rds_set_pi(self, *_: object, **__: object) -> None:
        return

    def rds_set_pty(self, *_: object, **__: object) -> None:
        return

    def rds_set_tp(self, *_: object, **__: object) -> None:
        return

    def rds_set_ta(self, *_: object, **__: object) -> None:
        return

    def rds_set_ms_music(self, *_: object, **__: object) -> None:
        return

    def rds_set_di(self, *_: object, **__: object) -> None:
        return

    def rds_set_ps(self, *_: object, **__: object) -> None:
        return

    def rds_set_pscount(self, *_: object, **__: object) -> None:
        return

    def set_rt_ab_mode(self, *_: object, **__: object) -> None:
        return

    def rds_set_rt(self, *_: object, **__: object) -> None:
        return

    def rds_enable(self, enabled: bool) -> None:
        self.rds_enabled = enabled

    def hw_reset(self, *_: object, **__: object) -> None:
        self._transmitting = False

    def is_transmitting(self) -> bool:
        return self._transmitting

    def tx_status(self) -> tuple[int, int, bool, int] | None:
        return (self.freq_10khz, self.power if self._transmitting else 0, False, 0)

    def read_asq(self) -> tuple[bool, int]:
        return False, 0

    def close(self) -> None:
        self.closed = True


def make_manager(config_root: Path, tx_cls: type[StubSI4713] = StubSI4713) -> TransmitterManager:
    return TransmitterManager(config_root=config_root, tx_factory=tx_cls)


@pytest.fixture()
def manager(tmp_path: Path) -> TransmitterManager:
    mgr = make_manager(tmp_path)
    yield mgr
    mgr.shutdown()


def test_apply_config_initialises_hardware(manager: TransmitterManager, tmp_path: Path) -> None:
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")

    status = manager.apply_config(Path("station.yml"))
    assert status["config_name"] == "station.yml"
    assert status["ps"].strip() == "TESTFM"
    assert status["rds"]["ps_current"].strip() == "TESTFM"
    assert status["rds"]["ps_active_index"] == 0
    assert status["broadcasting"] is True
    assert status["rds"]["pi"] == "0x1234"
    assert status["rds"]["tp"] is True
    assert status["audio"]["limiter_on"] is True
    assert status["audio"]["comp_thr"] == -26
    assert status["audio"]["comp_gain"] == 12
    assert status["audio"]["stereo"] is True
    assert status["audio"]["preemphasis_us"] == 50
    assert status["rds"]["enabled"] is True
    assert status["rds"]["configured"] is True
    assert isinstance(manager._tx, StubSI4713)  # type: ignore[attr-defined]
    assert manager._tx.audio_settings["comp_thr"] == -26  # type: ignore[attr-defined]
    assert manager._tx.audio_settings["limiter_on"] is True  # type: ignore[attr-defined]
    assert manager._tx.preemphasis_us == 50  # type: ignore[attr-defined]

    queue = manager.metrics_queue()
    event = queue.get(timeout=2)
    assert event["ps"].strip() == "TESTFM"
    assert event["rds"]["pi"] == "0x1234"
    assert event["rds"]["ps_current"].strip() == "TESTFM"
    assert event["audio"]["preemphasis_us"] == 50
    assert event["rds"]["ps_active_index"] == 0
    assert event["rds"]["configured"] is True
    assert event["audio"]["limiter_on"] is True
    assert event["audio"]["stereo"] is True
    assert event.get("audio_input_dbfs") is None
    manager.unregister_queue(queue)


def test_apply_config_with_relative_config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    relative_root = Path("configs")
    manager = make_manager(relative_root)

    cfg_path = relative_root / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")

    status = manager.apply_config(Path("station.yml"))
    assert status["config_name"] == "station.yml"

    manager.shutdown()


def test_write_config_rejects_invalid_yaml(manager: TransmitterManager) -> None:
    with pytest.raises(ValidationError):
        manager.write_config(Path("invalid.yml"), ":::bad:::yaml:::")


def test_read_config_struct_returns_full_payload(manager: TransmitterManager, tmp_path: Path) -> None:
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    data = manager.read_config_struct(Path("station.yml"))
    assert data["rf"]["frequency_khz"] == 98700
    assert data["rds"]["pi"] == "0x1234"
    assert data["audio"]["limiter_on"] is True
    assert data["audio"]["comp_thr"] == -26
    assert data["audio"]["stereo"] is True
    assert data["audio"]["preemphasis_us"] == 50
    assert data["rds"]["enabled"] is True
    assert data["rds"]["ps"] == ["TESTFM"]
    assert data["rds"]["di"] == {
        "stereo": True,
        "artificial_head": False,
        "compressed": False,
        "dynamic_pty": False,
    }
    assert data["rds"]["rt"]["texts"] == ["Enjoy the music"]
    assert data["monitor"]["health"] is False


def test_apply_config_respects_stereo_and_rds_flags(
    manager: TransmitterManager, tmp_path: Path
) -> None:
    cfg_path = tmp_path / "station.yml"
    custom_cfg = (
        SAMPLE_CFG.replace("stereo: true", "stereo: false")
        .replace("enabled: true", "enabled: false")
        .replace("preemphasis_us: 50", "preemphasis_us: 75")
    )
    cfg_path.write_text(custom_cfg, encoding="utf-8")

    status = manager.apply_config(Path("station.yml"))
    tx = manager._tx  # type: ignore[attr-defined]
    assert tx is not None
    assert tx.stereo_mode is False
    assert tx.rds_enabled is False
    assert status["audio"]["stereo"] is False
    assert status["rds"]["configured"] is False
    assert status["rds"]["enabled"] is False
    assert status["rt_source"] == "disabled"
    assert status["audio"]["preemphasis_us"] == 75
    assert tx.preemphasis_us == 75


def test_ps_rotation_advances_current_status(
    manager: TransmitterManager, tmp_path: Path
) -> None:
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(ROTATING_CFG, encoding="utf-8")

    status = manager.apply_config(Path("station.yml"))
    assert status["ps"].strip() == "FIRSTFM"
    assert status["rds"]["ps_active_index"] == 0
    assert status["rds"]["ps_current"].strip() == "FIRSTFM"

    if manager._watchdog_thread and manager._watchdog_thread.is_alive():  # type: ignore[attr-defined]
        manager._stop_event.set()  # type: ignore[attr-defined]
        manager._watchdog_thread.join(timeout=1)  # type: ignore[attr-defined]

    with manager._lock:  # type: ignore[attr-defined]
        cfg = manager._config  # type: ignore[attr-defined]
        assert cfg is not None
        manager._ps_next_tick = 0.0  # type: ignore[attr-defined]
        manager._maybe_rotate_ps(cfg)  # type: ignore[attr-defined]

    rotated = manager.current_status()
    assert rotated["ps"].strip() == "SECONDFM"
    assert rotated["rds"]["ps_active_index"] == 1
    assert rotated["rds"]["ps_current"].strip() == "SECONDFM"

    with manager._lock:  # type: ignore[attr-defined]
        cfg = manager._config  # type: ignore[attr-defined]
        assert cfg is not None
        manager._ps_next_tick = 0.0  # type: ignore[attr-defined]
        manager._maybe_rotate_ps(cfg)  # type: ignore[attr-defined]

    cycled = manager.current_status()
    assert cycled["ps"].strip() == "FIRSTFM"
    assert cycled["rds"]["ps_active_index"] == 0
    assert cycled["rds"]["ps_current"].strip() == "FIRSTFM"


def test_write_config_struct_roundtrip(manager: TransmitterManager, tmp_path: Path) -> None:
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    payload = manager.read_config_struct(Path("station.yml"))
    payload["rf"]["power"] = 100
    payload["audio"]["comp_gain"] = 20
    manager.write_config_struct(Path("copy.yml"), payload)
    raw = manager.read_config(Path("copy.yml"))
    assert "power: 100" in raw
    assert "pi: 0x1234" in raw
    assert "comp_gain: 20" in raw
    assert "preemphasis_us: 50" in raw


def test_write_config_struct_normalizes_ab_mode(manager: TransmitterManager, tmp_path: Path) -> None:
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    payload = manager.read_config_struct(Path("station.yml"))
    payload["rds"]["rt"]["ab_mode"] = "AUTO"
    payload["rds"]["rt"]["bank"] = "1"

    manager.write_config_struct(Path("auto.yml"), payload)
    raw = manager.read_config(Path("auto.yml"))

    assert "ab_mode: auto" in raw
    assert "bank:" not in raw


def test_write_config_struct_preserves_bank_mode(manager: TransmitterManager, tmp_path: Path) -> None:
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    payload = manager.read_config_struct(Path("station.yml"))
    payload["rds"]["rt"]["ab_mode"] = "bank"
    payload["rds"]["rt"]["bank"] = "1"

    manager.write_config_struct(Path("bank.yml"), payload)
    raw = manager.read_config(Path("bank.yml"))

    assert "ab_mode: bank" in raw
    assert "bank: 1" in raw


def test_write_config_struct_validates_ps(manager: TransmitterManager) -> None:
    payload = {
        "rf": {"frequency_khz": "98700", "power": "115", "antenna_cap": "4"},
        "rds": {
            "pi": "0x1234",
            "pty": "5",
            "tp": True,
            "ta": False,
            "ms_music": True,
            "ps": [],
            "ps_center": True,
            "ps_speed": "8",
            "ps_count": "1",
            "di": {
                "stereo": True,
                "artificial_head": False,
                "compressed": False,
                "dynamic_pty": False,
            },
            "rt": {
                "text": "",
                "texts": [],
                "speed_s": "10",
                "center": True,
                "file_path": "",
                "skip_words": [],
                "ab_mode": "auto",
                "repeats": "3",
                "gap_ms": "60",
                "bank": "",
            },
        },
        "monitor": {
            "health": True,
            "asq": True,
            "interval_s": "1.0",
            "recovery_attempts": "3",
            "recovery_backoff_s": "0.5",
        },
    }
    with pytest.raises(ValidationError):
        manager.write_config_struct(Path("bad.yml"), payload)


def test_toggle_broadcast_requires_applied_config(manager: TransmitterManager) -> None:
    with pytest.raises(ValidationError):
        manager.set_broadcast(True)


def test_toggle_broadcast_cycle(manager: TransmitterManager, tmp_path: Path) -> None:
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    manager.apply_config(Path("station.yml"))

    status = manager.set_broadcast(False)
    assert status["broadcasting"] is False
    assert status["watchdog_status"] == "stopped"
    assert status["rds"]["enabled"] is False
    assert status["rds"]["configured"] is True
    assert status["audio_input_dbfs"] is None
    assert status["audio"]["limiter_on"] is True
    assert status["audio"]["stereo"] is True
    assert status["audio"]["preemphasis_us"] == 50
    assert manager._tx is None  # type: ignore[attr-defined]

    status = manager.set_broadcast(True)
    assert status["broadcasting"] is True
    assert status["watchdog_status"] == "running"
    assert status["rds"]["enabled"] is True
    assert status["rds"]["configured"] is True
    assert status["audio"]["comp_thr"] == -26
    assert status["audio"]["stereo"] is True
    assert status["audio"]["preemphasis_us"] == 50


def test_apply_config_recovers_when_initial_tx_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from webapp import transmitter as txmod

    class IdleStub(StubSI4713):
        def is_transmitting(self) -> bool:  # type: ignore[override]
            return False

    recover_called = threading.Event()

    def fake_recover(tx: StubSI4713, cfg: object) -> bool:
        recover_called.set()
        tx.enable_mpx(True)
        return True

    monkeypatch.setattr(txmod, "recover_tx", fake_recover)

    manager = make_manager(tmp_path, IdleStub)
    cfg_text = SAMPLE_CFG.replace("health: false", "health: true")
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    status = manager.apply_config(Path("station.yml"))
    assert status["broadcasting"] is True
    assert recover_called.is_set()

    manager.shutdown()


def test_apply_config_raises_when_recover_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from webapp import transmitter as txmod

    class IdleStub(StubSI4713):
        def is_transmitting(self) -> bool:  # type: ignore[override]
            return False

    monkeypatch.setattr(txmod, "recover_tx", lambda *_: False)

    manager = make_manager(tmp_path, IdleStub)
    cfg_text = SAMPLE_CFG.replace("health: false", "health: true")
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    with pytest.raises(ValidationError):
        manager.apply_config(Path("station.yml"))

    manager.shutdown()


def test_watchdog_respects_health_grace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from webapp import transmitter as txmod

    class LateFailureStub(StubSI4713):
        def __init__(self) -> None:
            super().__init__()
            self._fail_at = time.monotonic() + 0.3

        def tx_status(self) -> tuple[int, int, bool, int] | None:  # type: ignore[override]
            if time.monotonic() >= self._fail_at:
                self._transmitting = False
                self.power = 0
            return super().tx_status()

    recover_called = threading.Event()

    def fake_recover(tx: StubSI4713, cfg: object) -> bool:
        recover_called.set()
        return False

    monkeypatch.setattr(txmod, "recover_tx", fake_recover)

    manager = make_manager(tmp_path, LateFailureStub)
    cfg_text = SAMPLE_CFG.replace("health: false", "health: true")
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(cfg_text, encoding="utf-8")

    manager.apply_config(Path("station.yml"))
    queue = manager.metrics_queue()
    queue.get(timeout=2)

    assert not recover_called.wait(0.5)
    assert recover_called.wait(1.5)

    manager.unregister_queue(queue)
    manager.shutdown()
