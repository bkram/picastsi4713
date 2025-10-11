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

  * System packages for Raspberry Pi hardware access (install on the Pi):

    ```bash
    sudo apt install python3-rpi.gpio python3-smbus
    ```

  * Python packages for the CLI + dashboard (install inside your virtual
    environment):

    ```bash
    pip install -r requirements.txt
    ```

    The `requirements.txt` file pins Flask and the shared runtime
    dependencies, while also including `RPi.GPIO` guarded behind ARM
    platform markers so development on non-Pi machines continues to work.

### Running

Insert audio into your SI4713 module, then run:

```bash
python3 run_tx.py --cfg cfg/picastsi4713.yml
```

### Launching the Web Dashboard

The Flask dashboard ships alongside the CLI runner so you can manage
profiles and watch telemetry from a browser. To start it locally:

1. Create and activate a virtual environment (optional but recommended):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

2. Install the Python requirements:

   ```bash
   pip install -r requirements.txt
   ```

   > When the requirements are installed on non-ARM hardware the
   > Raspberry Pi GPIO dependency is skipped automatically thanks to the
   > platform markers in `requirements.txt`.

3. Launch the dashboard with the bundled helper script:

   ```bash
   python run_dashboard.py --debug
   ```

   The script mirrors the environment variables used by `flask run`, so
   you can change the bind address, port, and configuration root either
   via CLI flags or the matching env vars:

   ```bash
   python run_dashboard.py --host 0.0.0.0 --port 5000 --config-root /path/to/cfg
   ```

   When `--debug` is omitted the server runs in production mode with the
   reloader disabled.

4. Open http://127.0.0.1:5000 in a browser to access the dashboard.

The background watchdog and broadcast controls mirror the CLI behaviour,
so any profile you apply from the UI immediately affects the live
transmitter.

> **Hardware requirement**
>
> The dashboard communicates with the same SI4713 hardware driver used by
> the CLI. Ensure the transmitter is connected and accessible on
> `/dev/i2c-1` before applying a profile or enabling broadcast.

---

## ⚙️ Configuration

PiCastSI4713 is controlled via a YAML configuration file.

An example is provided at **[`cfg/picastsi4713.yml`](cfg/picastsi4713.yml)**:

### Highlights

* **RF** → frequency, power, antenna capacitor
* **RDS** → PI/PTY codes, Program Service names (PS), and RadioText (RT)
* **RT rotation** → multiple texts, auto A/B switching, file override, filtering
* **Monitoring** → health checks, ASQ logging, automatic recovery

For deeper detail on the SI4713 power registers and how the CLI/web
layers map dBµV requests onto the underlying `TX_TUNE_POWER` command,
see [`docs/si4713-power.md`](docs/si4713-power.md).

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
