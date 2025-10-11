from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

pytest.importorskip("yaml")

from webapp.transmitter import TransmitterManager, ValidationError, VirtualSI4713


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


@pytest.fixture()
def manager(tmp_path: Path) -> TransmitterManager:
    return TransmitterManager(config_root=tmp_path)


def test_apply_config_uses_virtual_si4713(manager: TransmitterManager, tmp_path: Path):
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    status = manager.apply_config(Path("station.yml"))
    assert status["config_name"] == "station.yml"
    assert status["ps"].strip() == "TESTFM"
    assert status["broadcasting"] is True
    queue = manager.metrics_queue()
    event = queue.get(timeout=2)
    assert event["ps"].strip() == "TESTFM"
    manager.unregister_queue(queue)
    manager.shutdown()


def test_apply_config_with_relative_config_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    relative_root = Path("configs")
    manager = TransmitterManager(config_root=relative_root)
    cfg_path = relative_root / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")

    status = manager.apply_config(Path("station.yml"))
    assert status["config_name"] == "station.yml"

    manager.shutdown()


def test_write_config_rejects_invalid_yaml(manager: TransmitterManager):
    with pytest.raises(ValidationError):
        manager.write_config(Path("invalid.yml"), ":::bad:::yaml:::")


def test_read_config_struct_returns_full_payload(manager: TransmitterManager, tmp_path: Path):
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    data = manager.read_config_struct(Path("station.yml"))
    assert data["rf"]["frequency_khz"] == 98700
    assert data["rds"]["pi"] == 0x1234
    assert data["rds"]["ps"] == ["TESTFM"]
    assert data["rds"]["di"] == {
        "stereo": True,
        "artificial_head": False,
        "compressed": False,
        "dynamic_pty": False,
    }
    assert data["rds"]["rt"]["texts"] == ["Enjoy the music"]
    assert data["monitor"]["health"] is False


def test_write_config_struct_roundtrip(manager: TransmitterManager, tmp_path: Path):
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    payload = manager.read_config_struct(Path("station.yml"))
    payload["rf"]["power"] = 100
    manager.write_config_struct(Path("copy.yml"), payload)
    raw = manager.read_config(Path("copy.yml"))
    assert "power: 100" in raw


def test_write_config_struct_validates_ps(manager: TransmitterManager):
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


def test_toggle_broadcast_requires_applied_config(manager: TransmitterManager):
    with pytest.raises(ValidationError):
        manager.set_broadcast(True)


def test_toggle_broadcast_cycle(manager: TransmitterManager, tmp_path: Path):
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")
    manager.apply_config(Path("station.yml"))

    status = manager.set_broadcast(False)
    assert status["broadcasting"] is False
    assert status["watchdog_status"] == "paused"

    status = manager.set_broadcast(True)
    assert status["broadcasting"] is True
    assert status["watchdog_status"] == "running"


def test_apply_config_falls_back_to_virtual_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from webapp import transmitter as txmod

    class ExplodingSI4713:
        def init(self, *_: object, **__: object) -> bool:
            return True

        def set_output(self, *_: object, **__: object) -> None:
            return

        def set_frequency_10khz(self, *_: object, **__: object) -> None:
            return

        def enable_mpx(self, *_: object, **__: object) -> None:
            raise RuntimeError("i2c bus unavailable")

        def close(self) -> None:
            return

    monkeypatch.setattr(txmod, "HardwareSI4713", ExplodingSI4713)

    manager = TransmitterManager(config_root=tmp_path, prefer_virtual=False)
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")

    status = manager.apply_config(Path("station.yml"))
    assert status["broadcasting"] is True
    assert isinstance(manager._tx, VirtualSI4713)

    manager.shutdown()


def test_toggle_broadcast_recovers_with_virtual_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from webapp import transmitter as txmod

    class FlakySI4713(VirtualSI4713):
        def __init__(self) -> None:
            super().__init__()
            self._enable_attempts = 0

        def enable_mpx(self, on: bool) -> None:  # type: ignore[override]
            if on:
                self._enable_attempts += 1
                if self._enable_attempts >= 2:
                    raise RuntimeError("enable failure")
            super().enable_mpx(on)

    monkeypatch.setattr(txmod, "HardwareSI4713", FlakySI4713)

    manager = TransmitterManager(config_root=tmp_path, prefer_virtual=False)
    cfg_path = tmp_path / "station.yml"
    cfg_path.write_text(SAMPLE_CFG, encoding="utf-8")

    manager.apply_config(Path("station.yml"))
    status = manager.set_broadcast(False)
    assert status["broadcasting"] is False

    status = manager.set_broadcast(True)
    assert status["broadcasting"] is True
    assert isinstance(manager._tx, VirtualSI4713)

    manager.shutdown()


def test_apply_config_recovers_when_initial_tx_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from webapp import transmitter as txmod

    class IdleVirtual(txmod.VirtualSI4713):
        def is_transmitting(self) -> bool:  # type: ignore[override]
            return False

    recover_called = threading.Event()

    def fake_recover(tx: object, cfg: object) -> bool:
        recover_called.set()
        return True

    monkeypatch.setattr(txmod, "VirtualSI4713", IdleVirtual)
    monkeypatch.setattr(txmod, "recover_tx", fake_recover)

    manager = TransmitterManager(config_root=tmp_path, prefer_virtual=True)
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

    class IdleVirtual(txmod.VirtualSI4713):
        def is_transmitting(self) -> bool:  # type: ignore[override]
            return False

    monkeypatch.setattr(txmod, "VirtualSI4713", IdleVirtual)
    monkeypatch.setattr(txmod, "recover_tx", lambda *_: False)

    manager = TransmitterManager(config_root=tmp_path, prefer_virtual=True)
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

    class LateFailureVirtual(txmod.VirtualSI4713):
        def __init__(self) -> None:
            super().__init__()
            self._fail_at = time.monotonic() + 0.3

        def tx_status(self) -> tuple[int, int, bool, int] | None:  # type: ignore[override]
            if time.monotonic() >= self._fail_at:
                self._transmitting = False
                self._power = 0
            return super().tx_status()

    recover_called = threading.Event()

    def fake_recover(tx: object, cfg: object) -> bool:
        recover_called.set()
        return False

    monkeypatch.setattr(txmod, "VirtualSI4713", LateFailureVirtual)
    monkeypatch.setattr(txmod, "recover_tx", fake_recover)

    manager = TransmitterManager(config_root=tmp_path, prefer_virtual=True)
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
