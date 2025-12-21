# PiCastSI4713

PiCastSI4713 is an controller for the SI4713 FM transmitter and RDS encoder. It supports live JSON
config reloads, a web UI, recovery monitoring, and optional UECP input for
external RDS input.

## Features

- 📡 FM transmit control: frequency, power, antenna capacitor (manual or auto)
- 🎵 RDS: PI/PTY/TP/TA/MS/DI, PS rotation, RT rotation, RT file override, single AF
- 🌐 UECP input (TCP/UDP) for external RDS sources
- 🔄 Hot reload: apply config diffs without restarting
- 🛡️ Health monitoring with recovery attempts and ASQ logging
- 🎧 Optional audio stream playback per station config

## Requirements

- Python 3.8+
- Hardware: Raspberry Pi (I2C) or FT232H (USB-I2C)
- Dependencies: `pip install -r requirements.txt`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Headless (no web UI):

```bash
./run.sh
```

With web UI/API (default http://0.0.0.0:5080):

```bash
./run_web.sh
```

Optional:
- Use another station config: `CFG=cfg/YourStation.json ./run_web.sh`
- Start TX immediately: `./run_web.sh --start`
- Override adapter config: `ADAPTER_CFG=cfg/config.yaml ./run_web.sh`

## Configuration

There are two config files:

- Adapter config: `cfg/config.yaml` (hardware, FTDI URL, reset pin, API host/port,
  audio player command)
- Station config: `cfg/*.json` (RF, RDS, UECP, monitoring, streaming)

Only JSON station configs are supported. The last selected config and TX state are
stored in `cfg/state.json`.

Minimal station config example:

```json
{
  "rf": {
    "frequency_khz": 98700,
    "power": 115,
    "antenna_cap_auto": true,
    "audio_deviation_hz": 7500,
    "preemphasis": "us50"
  },
  "rds": {
    "enabled": true,
    "pi": "0x1234",
    "pty": 10,
    "tp": true,
    "ta": false,
    "ms_music": true,
    "di": { "stereo": true, "compressed": false },
    "ps": ["STATION"],
    "ps_center": true,
    "ps_speed": 10,
    "deviation_hz": 200,
    "rt": {
      "texts": ["Welcome"],
      "speed_s": 10,
      "center": true,
      "ab_mode": "auto",
      "repeats": 3,
      "gap_ms": 60,
      "file_path": ""
    }
  },
  "uecp": { "enabled": false, "host": "0.0.0.0", "port": 9100 },
  "streaming": { "enabled": false, "url": "" },
  "monitor": { "health": true, "asq": true, "interval_s": 1.0 }
}
```

Notes:
- `rf.frequency_khz` is in kHz (e.g., 98700 for 98.7 MHz).
- Pre-emphasis: prefer EU 50 us (`preemphasis: "us50"`). Use `us75` for US or `none` to disable.
- `rds.deviation_hz` is in 10 Hz units (e.g., 200 = 2.00 kHz).
- RT file override: set `rds.rt.file_path`; it overrides the RT list when present.
- Macros: `{time}`, `{date}`, `{datetime}`, `{config}`, `{freq}`, `{power}` in PS/RT texts.
- When `uecp.enabled` is true, `rds.enabled` is forced on.

Audio stream playback is configured per station in JSON and globally in the
adapter config. Example per-station block:

```json
"streaming": { "enabled": true, "url": "http://example/stream" }
```

MPV configuration (in `cfg/config.yaml`):

```yaml
audio_player_cmd: mpv
audio_player_device_flag: --no-video --cache=yes --cache-secs=10 --audio-device={device}
audio_player_url_arg: "{url}"
audio_device: coreaudio/AppleUSBAudioEngine:Unknown Manufacturer:USB Sound Device:2114000:1
```

List MPV audio devices:

```bash
mpv --audio-device=help
```

## UECP mode (external RDS, experimental)

UECP mode is experimental. It accepts binary UECP frames over TCP or UDP and applies only the fields
the SI4713 supports: PI, PTY, TP, TA, MS, DI, PS, RT. Internal RDS updates are
ignored while UECP is enabled. The SI4713 only supports 32 chars of RT; UECP RT
payloads are truncated to 32.

Enable UECP in the station config:

```json
"uecp": { "enabled": true, "host": "0.0.0.0", "port": 9100 }
```

The listener binds on the given host/port and accepts both TCP and UDP on the same
port.

Use any UECP-capable encoder (for example, a broadcast processor that emits
UECP frames). Configure it to send TCP or UDP to the host/port above.

## Web UI

The web UI is served by `run_web.sh`. Host/port are configured in `cfg/config.yaml`
as `api_host` and `api_port`. The UI auto-saves edits (debounced), can apply the
active config without restarting audio, includes an On Air toggle, and shows a
live console log stream.

## Supported platforms

- Raspberry Pi (I2C via smbus2): supported.
- FT232H (USB-I2C via pyftdi or Blinka) on Linux/macOS: supported.
- FT232H on Windows: experimental (requires WinUSB/libusb driver setup).

## Hardware notes

| Platform | SDA | SCL | RESET | Notes |
| --- | --- | --- | --- | --- |
| Raspberry Pi (I2C bus 1) | GPIO 2 | GPIO 3 | GPIO 5 (default) | 3.3V only, common ground |
| FT232H (USB-I2C) | D1 | D0 | D5 (default) | 3.3V only, common ground; set `adapter: ft232h` or `adapter: ft232h_blinka` |

RESET is configurable via `SI4713_RESET_PIN` (RPi) or `ftdi_reset_pin` (FT232H).

## License

GPL-3.0. See `LICENSE`.

## Acknowledgements

- PE5PVB SI4713 Arduino project: https://github.com/PE5PVB/si4713
- Adafruit SI4713 documentation: https://learn.adafruit.com/adafruit-si4713-fm-radio-transmitter
