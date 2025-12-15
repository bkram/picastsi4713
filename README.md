# PiCastSI4713

A Python runner for the **SI4713 FM + RDS transmitter**, with live configuration reloads, RadioText A/B behavior, file overrides, and health monitoring.
Perfect for Raspberry Pi + SI4713 radio projects, or USB I²C via FT232H.

> ⚡ This project is a **Python port** inspired by the original **Arduino SI4713 code** from [PE5PVB](https://github.com/PE5PVB/si4713), extended with hot-reload configs, PS/RT handling, and recovery.

---

## ✨ Features

* 📡 **FM Transmission** with configurable frequency, power, and antenna tuning
* 🎵 **RDS Support**: PI, PTY, TP/TA, DI flags, PS scrolling, and RadioText
* 🔄 **RadioText A/B** switching (`legacy`, `auto`, or `bank` mode)
* ⚡ **Burst repeats** on change for faster pickup
* 🔧 **Hot reload**: live diff-only config updates (JSON configs)
* 🛡️ **Health monitoring** with recovery attempts and ASQ logging (optional overmod ignore threshold)
* 📝 **File override** for RadioText, with word-skip filters
* 🎛️ **Centering options** for PS and RT

---

## 🚀 Getting Started

### Requirements

* Python **3.8+**
* Hardware: Raspberry Pi (GPIO + I²C) **or** FT232H (USB‑I²C)
* [Adafruit SI4713 breakout](https://www.adafruit.com/product/1958) or compatible module
* Install deps via pip:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  pip install -r requirements.in
  ```
  - smbus2 (RPi)
  - pyftdi, adafruit-blinka (FT232H)
  - flask (web UI)

### Launchers

- **Headless (no web UI):**
  ```bash
  ./run.sh
  ```
- **With web UI/API (default http://0.0.0.0:5080):**
  ```bash
  ./run_web.sh
  ```

The adapter/web defaults are in `cfg/config.yaml` (adapter type, FTDI URL, reset pin, API host/port). The station config defaults to `cfg/default.json`. To use another config, set `CFG=/path/to/your.json` when running the scripts.

### FT232H (USB-I²C) host

If you are driving the SI4713 from a computer via an **FT232H** instead of Raspberry Pi GPIO:

1) Wire FT232H D1/D2 to SCL/SDA and pick a spare GPIO line (e.g., D5) for **RESET**. Keep everything at 3.3 V and ensure pull-ups on SDA/SCL (Adafruit SI4713 boards include them).
2) Pick a backend:

- **pyftdi backend (default)**  
  Make sure `pyftdi` is installed. Run with the defaults from `cfg/config.yaml`:  
  ```bash
  ./run.sh
  ```  
  Override via env or flags: `SI4713_FT232H_URL`, `SI4713_FT232H_RESET_PIN`, `--backend/--ftdi-url/--ftdi-reset-pin`.

- **Blinka backend (Adafruit style)**  
  Set `BLINKA_FT232H=1` or set `adapter: ft232h_blinka` in `cfg/config.yaml`, then use `./run.sh` or `./run_web.sh`.

**FT232H ↔ SI4713 pin map (3.3 V only)**

| FT232H pin   | SI4713 pin | Notes                                          |
| ------------ | ---------- | ---------------------------------------------- |
| D0 (SCL)     | SCL        | I²C clock                                      |
| D1 (SDA)     | SDA        | I²C data                                       |
| D5 (default) | RESET      | Use `RESET_PIN` to change                      |
| 3V3  or 5V   | VIN        | Power                                          |
| GND          | GND        | Common ground                                  |
| (SEN)        | SEN        | Keep high (usually on-board)                   |
| pull-ups     | SDA/SCL    | Present on Adafruit SI4713; add ~4.7 kΩ if not |

**Raspberry Pi ↔ SI4713 pin map (3.3 V only)**

| Raspberry Pi   | SI4713 pin | Notes                                          |
| -------------- | ---------- | ---------------------------------------------- |
| GPIO 3 (SCL)   | SCL        | I²C clock (bus 1)                              |
| GPIO 2 (SDA)   | SDA        | I²C data (bus 1)                               |
| GPIO 5 (BCM 5) | RESET      | Default reset; change with `SI4713_RESET_PIN`  |
| 3v3 or 5V      | VIN        | Power                                          |
| GND            | GND        | Common ground                                  |
| SEN            | SEN        | Keep high (often tied on board)                |
| pull-ups       | SDA/SCL    | Present on Adafruit SI4713; add ~4.7 kΩ if not |

---

## ⚙️ Configuration

PiCastSI4713 is controlled via a **JSON** configuration file. The default is **`cfg/default.json`**; state is persisted in `cfg/state.json` (not selectable).

### Highlights

* **RF** → frequency, power (88–120 dBµV), antenna capacitor (manual or auto)
* **RDS** → PI (hex), PTY, TP/TA/MS flags, DI flags, PS, RT
* **RT rotation** → multiple texts, auto/bank A/B switching, file override, filtering
* **Monitoring** → health checks, ASQ logging, automatic recovery, overmod ignore threshold (dBFS, default -5)
* **RDS deviation** → default 200 (2.00 kHz). Lower for a bit more audio headroom; raise slightly if RDS decoding is weak (watch overmod).
* **RDS toggle** → enable/disable RDS entirely via config/UI when you want FM only.
* **Macros** → use `{time}`, `{date}`, `{datetime}`, `{config}` in PS/RT texts; values refresh automatically. Separate PS/RT lists with `|` in the UI (pipes, no escaping needed).
* **Audio deviation control** → set `rf.audio_deviation_hz` (base) and optional `rf.audio_deviation_no_rds_hz` to reclaim headroom when RDS is off.
* **Pre-emphasis** → default EU 50 µs (`rf.preemphasis: "us50"`); choose `us75` for US or `none`.
* **Audio stream playback** → per-config `streaming.enabled/url`; player command template and device live in `cfg/config.yaml` (`audio_player_cmd`, `audio_player_device_flag`, `audio_device`).

To add a new config:
- **Web UI:** Use Import (JSON) or duplicate an existing profile, edit, then **Set Active**.
- **Manual:** Copy `cfg/default.json` to a new name in `cfg/`, edit, and either select it in the web UI or run with `CFG=cfg/YourConfig.json ./run_web.sh`.

**Auto antenna capacitance:** Set `rf.antenna_cap_auto` to `true` (or `rf.antenna_cap` to `"auto"`) to let the SI4713 pick the capacitor value automatically, matching the Adafruit examples.

**Audio deviation / “louder without RDS”:** Use `rf.audio_deviation_hz` (default 7500) for normal operation. To claw back headroom when RDS is disabled, set `rf.audio_deviation_no_rds_hz` (e.g., 8200) — it is only applied when `rds.enabled` is false.

**PS/RT macros:** You can embed `{time}` (HH:MM), `{date}`, `{datetime}`, or `{config}` inside PS or RT texts (including RT file contents). They auto-refresh while running.

**Audio stream playback:** Put a `streaming` block in each station config:

```json
"streaming": { "enabled": true, "url": "http://example/stream" }
```

The player command template and device are global in `cfg/config.yaml`:

```yaml
audio_player_cmd: "/opt/homebrew/bin/mpv"
audio_player_device_flag: "--no-video --cache=yes --cache-secs=10 --audio-device={device}"
audio_player_url_arg: "{url}"
audio_device: "coreaudio/AppleUSBAudioEngine:Unknown Manufacturer:USB Sound Device:2114000:1"
```

Only the URL is per-config; the audio device is global. `{device}` and `{url}` are substituted into the flag/arg templates, with quoting handled safely when the command is spawned.

**Find audio devices (mpv):**

```bash
mpv --audio-device=help
```

---

## 📖 Usage Notes

* Place a RadioText override in `rt_file.txt` to dynamically update RT.
* Config changes in JSON files are hot-reloaded (diff applied).
* The script attempts recovery automatically if the transmitter stalls.

---

## ❤️ Acknowledgments

* **[PE5PVB](https://github.com/PE5PVB/si4713)** for the original Arduino SI4713 code that inspired this project
* [Adafruit SI4713](https://learn.adafruit.com/adafruit-si4713-fm-radio-transmitter) for hardware documentation

## 📜 License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0).
See the LICENSE.
