# PiCastSI4713

A Python runner for the **SI4713 FM + RDS transmitter**, with live configuration reloads, RadioText A/B behavior, file overrides, and health monitoring.
Perfect for Raspberry Pi + SI4713 radio projects.

> ⚡ This project is a **Python port** inspired by the original **Arduino SI4713 code** from [PE5PVB](https://github.com/PE5PVB/si4713).
> We extended it with higher-level features like config hot-reload, PS/RT handling, and recovery.

---

## ✨ Features

* 📡 **FM Transmission** with configurable frequency, power, and antenna tuning
* 🎵 **RDS Support**: PI, PTY, TP/TA, DI flags, PS scrolling, and RadioText
* 🔄 **RadioText A/B** switching (`legacy`, `auto`, or `bank` mode)
* ⚡ **Burst repeats** on change for faster pickup
* 🔧 **Hot reload**: live diff-only config updates via YAML
* 🛡️ **Health monitoring** with recovery attempts and ASQ logging
* 📝 **File override** for RadioText, with word-skip filters
* 🎛️ **Centering options** for PS and RT

---

## 🚀 Getting Started

### Requirements

* Python **3.8+**
* Raspberry Pi (or any Linux board with GPIO + I²C)
* [Adafruit SI4713 breakout](https://www.adafruit.com/product/1958) or compatible module
* Dependencies:

```bash
sudo apt install python3-rpi.gpio python3-smbus python3-yaml
```

### Running

Insert audio into your SI4713 module, then run:

```bash
python3 run_tx.py --cfg cfg/picastsi4713.yml
```

### Virtualenv quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.in
```

Then start with the FT232H helper (Blinka backend):
```bash
./start_ft232h.sh
```
Override defaults if needed, e.g. `RESET_PIN=6 FTDI_URL=ftdi://ftdi:232h/1 ./start_ft232h.sh`.

### FT232H (USB-I²C) host

If you are driving the SI4713 from a computer via an **FT232H** instead of Raspberry Pi GPIO:

1) Wire FT232H D1/D2 to SCL/SDA and pick a spare GPIO line (e.g., D5) for **RESET**. Keep everything at 3.3 V and ensure pull-ups on SDA/SCL (Adafruit SI4713 boards include them).
2) Pick a backend:

- **pyftdi backend (default)**  
  Install `pyftdi`: `pip install pyftdi`  
  Run:  
  ```bash
  SI4713_BACKEND=ft232h \
  SI4713_FT232H_URL=ftdi://ftdi:232h/1 \
  SI4713_FT232H_RESET_PIN=5 \
  python3 run_tx.py --cfg cfg/picastsi4713.yml
  ```

- **Blinka backend (matches Adafruit FT232H examples)**  
  Install `adafruit-blinka` (pulls in `pyftdi`): `pip install adafruit-blinka`  
  Set `BLINKA_FT232H=1` and run:  
  ```bash
  BLINKA_FT232H=1 \
  SI4713_BACKEND=ft232h_blinka \
  SI4713_FT232H_RESET_PIN=5 \
  python3 run_tx.py --cfg cfg/picastsi4713.yml
  ```

You can also pass these via CLI: `--backend`, `--ftdi-url`, `--ftdi-reset-pin`.

**FT232H ↔ SI4713 pin map (3.3 V only)**

| FT232H pin | SI4713 pin | Notes                |
|------------|------------|----------------------|
| D1 (SCL)   | SCL        | I²C clock            |
| D2 (SDA)   | SDA        | I²C data             |
| D5 (default) | RESET    | Use `RESET_PIN` to change |
| 3V3        | VIN/3V3    | Power (3.3 V only)   |
| GND        | GND        | Common ground        |
| (SEN)      | SEN        | Keep high (usually on-board) |
| pull-ups   | SDA/SCL    | Present on Adafruit SI4713; add ~4.7 kΩ if not |

---

## ⚙️ Configuration

PiCastSI4713 is controlled via a YAML configuration file.

An example is provided at **[`cfg/picastsi4713.yml`](cfg/picastsi4713.yml)**:

### Highlights

* **RF** → frequency, power, antenna capacitor
* **RDS** → PI/PTY codes, Program Service names (PS), and RadioText (RT)
* **RT rotation** → multiple texts, auto A/B switching, file override, filtering
* **Monitoring** → health checks, ASQ logging, automatic recovery

---

## 📖 Usage Notes

* Place a RadioText override in `rt_file.txt` to dynamically update RT.
* Config changes in `cfg/picastsi4713.yml` are hot-reloaded (only diffs applied).
* The script attempts recovery automatically if the transmitter stalls.

---

## ❤️ Acknowledgments

* **[PE5PVB](https://github.com/PE5PVB/si4713)** for the original Arduino SI4713 code that inspired this project
* [Adafruit SI4713](https://learn.adafruit.com/adafruit-si4713-fm-radio-transmitter) for hardware documentation

## 📜 License

This project is licensed under the GNU General Public License v3.0 (GPL-3.0).
See the LICENSE
