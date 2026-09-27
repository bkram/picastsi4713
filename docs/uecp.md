# UECP input (external RDS)

PiCastSI4713 can receive UECP (Universal Encoder Communication Protocol,
EBU SPB 490) frames over **TCP or UDP** and apply them live to the SI4713.
Use this when an external RDS source (e.g. a broadcast processor, or the
included `docs/rdsd.py` daemon) should drive the RDS data instead of the
station config.

> Status: experimental.

## Enabling

Station config (`cfg/*.json`):

```json
"uecp": {
  "enabled": true,
  "host": "0.0.0.0",
  "port": 9100,
  "site_id": 1,
  "encoder_id": 1
}
```

| Key          | Default     | Meaning |
| ------------ | ----------- | ------- |
| `enabled`    | `false`     | Start the UECP listeners. Forces `rds.enabled` on. |
| `host`       | `"0.0.0.0"` | Bind address for TCP **and** UDP (same port). |
| `port`       | `9100`      | Bind port (1–65535). |
| `site_id`    | `null`      | Optional 0–1023. When set, only frames addressed to this site (or broadcast site 0) are applied. |
| `encoder_id` | `null`      | Optional 0–63. When set, only frames addressed to this encoder (or broadcast encoder 0) are applied. |

When `site_id`/`encoder_id` are omitted (`null`), **all** valid frames are
applied (legacy behaviour).

## Supported MECs

The SI4713 hardware limits what can be applied; unsupported UECP messages
are ignored silently.

| MEC    | Name   | Applied to SI4713 |
| ------ | ------ | ----------------- |
| `0x01` | PI     | `rds_set_pi` |
| `0x02` | PS     | `rds_set_ps` (8 chars) |
| `0x03` | TP/TA  | `rds_set_tp`, `rds_set_ta` |
| `0x04` | DI     | `rds_set_di` (stereo, artificial head, compressed, dynamic PTY) |
| `0x05` | M/S    | `rds_set_ms_music` |
| `0x06` | PIN    | type 1A group via raw FIFO (block C = PIN) |
| `0x07` | PTY    | `rds_set_pty` |
| `0x0A` | RT     | `rds_set_rt` — truncated to 32 chars (chip limit), A/B toggle bit honoured |
| `0x0D` | RTC    | encoder clock → CT group (type 4A), re-sent when the minute changes |
| `0x13` | AF     | `rds_set_af` (variants 0x05/0x07/0x0F, single AF code) |
| `0x19` | CT On/Off | enable/disable transmission of the 4A groups from `0x0D` |
| `0x1E` | RDS On/Off | `rds_enable` (RDS subcarrier on/off) |
| `0x24` | Free-format group | streamed verbatim via raw FIFO (any group type/version; PI filled by chip) |
| `0x3E` | PTYN   | two type 10A groups via raw FIFO (A/B toggle on text change) |

CT notes: the SI4713 has no built-in CT scheduler, so 4A groups are generated
in software from the encoder's RTC messages — one group per minute change
(encoders re-send RTC frequently, so this tracks the minute edge closely).
`0x19 00` stops CT transmission; `0x19 01` resumes it and re-sends immediately.

**Not implemented**: EON (`0x14`), TMC (`0x1B`), TDC (`0x0C`), EWS (`0x08`),
IH (`0x20`), slow labeling (`0x1A`), linkage info (`0x2E`), ODA commands.
Note that EWS/TDC/IH/TMC and ODAs can already be aired by the sender using
the **free-format group (`0x24`)** passthrough. Long RT (>32 chars) is
truncated — hardware limit.

While UECP is enabled, internal RDS updates (PS/RT rotation, RT file) are
suspended; the UECP source owns the RDS data.

## Wire format (what the decoder accepts)

```
FE <ADD:2> <SEQ:1> <MEL:1> <DATA:MEL> <CRC:2> FF
```

- Bytes `FE`, `FF`, `FD` inside the frame are stuffed: `FD 00`=FD, `FD 01`=FE, `FD 02`=FF.
- CRC is CRC-16/CCITT over the unstuffed `ADD..DATA`, initial `0xFFFF`,
  final complement, transmitted big-endian.
- `ADD`: 10-bit site address (high bits) + 6-bit encoder address.
- `SEQ`: frame counter; the receiver logs (debug) gaps to help detect UDP loss.
- Frames failing CRC or length checks are dropped (debug log).

## Testing with the included UECP sender

`docs/rdsd.py` is a small UECP encoder daemon (YAML-driven) that can feed
PiCastSI4713 over UDP/TCP — handy for end-to-end testing:

```bash
cd docs
python3 rdsd.py rdsd-example-udp.yaml        # sends UDP to 127.0.0.1:9100
# or replay a captured UECP stream:
python3 replay.py
```

Example configs: `docs/rdsd-example-udp.yaml`, `docs/rdsd-example-serial.yaml`.
See `docs/profline-pi.md` for hardware-specific notes.

Enable `LOG_LEVEL=DEBUG` on the receiver to see per-frame logs, address
filter decisions, and sequence gaps:

```bash
LOG_LEVEL=DEBUG ./run_web.sh
```

## Troubleshooting

| Symptom | Likely cause |
| ------- | ------------ |
| Nothing applied | Wrong host/port; sender targeting another site/encoder than configured; frames failing CRC (enable DEBUG). |
| Works on TCP, not UDP | Firewall/NAT; UDP is connectionless — check with `nc -u`. |
| "UECP sequence gap" in debug log | UDP packet loss (expected occasionally) or a rebooting sender. |
| RT truncated | SI4713 supports 32 chars max — shorten the RT or enable CR termination. |
