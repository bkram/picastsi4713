# AGENTS.md — guide for AI agents working in this repo

## Project overview

PiCastSI4713 is a controller for the SI4713 FM transmitter + RDS encoder.
Python 3.8+ (uses `from __future__ import annotations` everywhere — PEP 604
unions and builtin generics in annotations are fine and preferred).

| Path | Role |
|---|---|
| `picast4713.py` | Main controller: config model, main loop, RT/PS rotation, health/recovery, UECP receiver (`UecpBridge`), RT+ |
| `si4713/__init__.py` | Hardware driver: I2C command layer, RPi/smbus2 + FT232H/pyftdi + Blinka backends, raw RDS FIFO (`rds_send_group`) |
| `si4713/charset.py` | RDS character set codec (IEC 62106 Annex E, table E.1) |
| `web/__init__.py` | Flask API + status buses (`StatusBus`, `LogBus`) |
| `web/static/` | Web UI assets (no Python) |
| `cfg/` | Station configs (`*.json`), adapter config (`config.yaml`, gitignored), `state.json` (gitignored) |
| `docs/` | Project docs + reference tools (`rdsd.py`, `uecprds/`), captures (mostly gitignored; `docs/uecp.md` is tracked) |
| `standards/` | Normative specs (EN 50067, IEC 62106, SPB 490) — PDFs not tracked |

## Toolchain (ruff)

Ruff is the single linter + formatter. Config: `ruff.toml`
(target py38, line-length 88, rules: `E,W,F,I,UP,B,SIM,D` with Google
docstring convention).

```bash
pip install -r requirements-dev.txt   # installs ruff

ruff check picast4713.py si4713/ web/          # lint
ruff format --check picast4713.py si4713/ web/ # formatting check
ruff format picast4713.py si4713/ web/         # apply formatting
```

**Both must be clean before committing.** Do not add `# noqa`/`type: ignore`
without a short comment explaining why (optional hardware imports use
`TYPE_CHECKING` guards instead).

## Conventions

- **Text encoding**: never use `.encode("latin-1")`/`ord()` for on-air text.
  Use `si4713.charset.encode()` (Unicode → RDS codes) and `.decode()` for
  display. The RDS repertoire is NOT Latin-1.
- **Threading**: `SI4713` public methods must hold `self.lock` (an `RLock`)
  across compose+write and read-modify-write. Do NOT reintroduce shared
  command buffers — compose in local lists, pass to `_write_buf(data)`.
  Threads: main loop, UECP TCP/UDP, Flask requests.
- **Raw RDS groups** (CT/PTYN/PIN/free-format/RT+) go through
  `SI4713.rds_send_group()`; verify bit layouts against `standards/`
  (EN 50067 figures) and the redsea decoder.
- **Config parsing**: use `_parse_int/_parse_bool/_parse_float` helpers in
  `AppConfig` — never raw `int()`/`float()` on user config.
- **State file** (`cfg/state.json`): always write atomically (tmp + replace).

## Verification (no hardware needed)

There is no pytest suite yet; validate changes with inline scripts:

```bash
# 1. compile + lint + format
python3 -m py_compile picast4713.py si4713/__init__.py web/__init__.py
ruff check picast4713.py si4713/ web/
ruff format --check picast4713.py si4713/ web/

# 2. replay the real UECP encoder capture end-to-end (docs/captures/)
#    expected: PI 0x1337, PTY 15, TP/TA, DI, PS scrolling, RT (full text),
#    AF 97, exactly 5 CT groups (minutes 20:01,02,05,06,07)
python3 - <<'EOF'
import threading, time
from picast4713 import AppConfig, UecpBridge, UecpState
calls = []
class FakeTx:
    def __getattr__(self, n):
        def f(*a, **k): calls.append((n,) + a); return True
        return f
cfg = AppConfig({'rf': {'frequency_khz': 100000, 'power': 100},
                 'rds': {'pi': 1, 'pty': 1, 'ps': ['X']},
                 'uecp': {'enabled': True}})
b = UecpBridge.__new__(UecpBridge)
b._tx = FakeTx(); b._cfg = cfg; b._status_bus = None
b._stop_event = threading.Event(); b._local_stop = threading.Event()
b._lock = threading.Lock(); b._threads = []
b._last_payloads = {}; b._last_seq = None; b._ptyn_ab = 0
b._state = UecpState()
orig = time.sleep; time.sleep = lambda s: None
try:
    b._handle_stream(bytearray(), open('docs/captures/raw_uecp_capture.bin','rb').read())
    assert ('rds_set_pi', 0x1337) in calls
    assert ('rds_set_af', 97) in calls
    assert sum(1 for x in calls if x[0] == 'rds_send_ct') == 5
finally:
    time.sleep = orig
print('OK')
EOF
```

Mock the I2C bus (record `write_i2c_block_data`) to assert on exact on-air
bytes for driver-level changes. The capture is the ground truth for UECP
framing — run it after ANY change to `UecpBridge`.

## Commits / PRs

- Work happens on the `fixes` branch; small focused commits with a body
  explaining the *why* (see `git log` for style).
- `cfg/config.yaml`, `cfg/state.json`, `docs/` (except `docs/uecp.md`) and
  `standards/*.pdf` are gitignored — do not force-add them.
