from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

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
