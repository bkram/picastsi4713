import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("yaml")

from webapp.transmitter import TransmitterManager, ValidationError


SAMPLE_CFG = """
rf:
  frequency_khz: 98700
  power: 115
  antenna_cap: 4
rds:
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
  deviation_hz: 200
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


class StubSI4713:
    def __init__(self) -> None:
        self._transmitting = False
        self.freq_10khz = 0
        self.power = 0
        self.antenna = 0
        self.closed = False

    def init(self, *_: object, **__: object) -> bool:
        return True

    def set_output(self, power: int, antenna: int) -> None:
        self.power = power
        self.antenna = antenna

    def set_frequency_10khz(self, freq: int) -> None:
        self.freq_10khz = freq

    def enable_mpx(self, on: bool) -> None:
        self._transmitting = on

    def set_pilot(self, *_: object, **__: object) -> None:
        return

    def set_audio(self, *_: object, **__: object) -> None:
        return

    def set_audio_processing(self, *_: object, **__: object) -> None:
        return

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

    def rds_set_deviation(self, *_: object, **__: object) -> None:
        return

    def rds_set_ps(self, *_: object, **__: object) -> None:
        return

    def rds_set_pscount(self, *_: object, **__: object) -> None:
        return

    def set_rt_ab_mode(self, *_: object, **__: object) -> None:
        return

    def rds_set_rt(self, *_: object, **__: object) -> None:
        return

    def rds_enable(self, *_: object, **__: object) -> None:
        return

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
    assert status["broadcasting"] is True
    assert isinstance(manager._tx, StubSI4713)  # type: ignore[attr-defined]

    queue = manager.metrics_queue()
    event = queue.get(timeout=2)
    assert event["ps"].strip() == "TESTFM"
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
    assert data["rds"]["ps"] == ["TESTFM"]
    assert data["rds"]["di"] == {
        "stereo": True,
        "artificial_head": False,
        "compressed": False,
        "dynamic_pty": False,
    }
    assert data["rds"]["rt"]["texts"] == ["Enjoy the music"]
    assert data["monitor"]["health"] is False


def test_write_config_struct_roundtrip(manager: TransmitterManager, tmp_path: Path) -> None:
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    payload = manager.read_config_struct(Path("station.yml"))
    payload["rf"]["power"] = 100
    manager.write_config_struct(Path("copy.yml"), payload)
    raw = manager.read_config(Path("copy.yml"))
    assert "power: 100" in raw
    assert "pi: 0x1234" in raw


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
            "deviation_hz": "200",
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
    assert status["watchdog_status"] == "paused"

    status = manager.set_broadcast(True)
    assert status["broadcasting"] is True
    assert status["watchdog_status"] == "running"


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
