# SI4713 Feature Coverage Audit

This document cross-checks the PiCast SI4713 dashboard against the capabilities that the codebase exposes from Silicon Labs' SI4713 FM transmitter. It highlights what the UI currently surfaces, what the backend supports but the UI omits, and verifies that the values presented to operators match the transmitter controls.

## Methodology

- Reviewed the low-level driver in `si4713/__init__.py` to catalogue the callable hardware features.
- Traced how `picast4713.apply_config` and `TransmitterManager` apply those features at runtime.
- Mapped each dashboard control in `webapp/templates/index.html` and the client logic in `webapp/static/js/main.js` to the underlying configuration fields.

## Feature Coverage Summary

| Domain | SI4713 capability (per driver) | UI / manager coverage | Notes |
| --- | --- | --- | --- |
| RF carrier | Frequency (10 kHz resolution), output power, antenna tuning via `set_frequency_10khz` and `set_output`. | Frequency, power, and capacitance are editable in the RF card and stored in configs. | `TransmitterManager` persists `frequency_khz`, `power`, and `antenna_cap`, and the RF tab exposes matching inputs.【F:si4713/__init__.py†L126-L141】【F:webapp/transmitter.py†L246-L260】【F:webapp/templates/index.html†L143-L168】 |
| Composite enable | MPX modulation toggled via `enable_mpx`. | Always enabled when applying a profile; no UI toggle. | Dashboard assumes stereo MPX operation because `apply_config` hard-enables the composite path without exposing a switch.【F:picast4713.py†L357-L365】 |
| Pilot and baseband | Pilot tone frequency/deviation and audio deviation & pre-emphasis via `set_pilot` and `set_audio`. | Hard-coded to 19 kHz pilot, 6.75 kHz deviation, 75 kHz audio deviation, and 50 µs pre-emphasis. | Operators cannot change these defaults in the UI; adjustability would require new form controls and config fields.【F:si4713/__init__.py†L150-L162】【F:picast4713.py†L363-L366】 |
| Audio processing | AGC, limiter, compressor threshold/attack/release/gain, limiter release via `set_audio_processing`. | Fully surfaced in the Audio tab with presets and manual overrides. | Form fields map directly to serialized config keys and round-trip through the manager and CLI helpers.【F:si4713/__init__.py†L164-L187】【F:webapp/transmitter.py†L253-L260】【F:webapp/templates/index.html†L248-L289】【F:webapp/static/js/main.js†L42-L99】 |
| RDS identity & flags | PI, PTY, TP, TA, MS, DI flags, and deviation via `rds_set_pi`, `rds_set_pty`, `rds_set_tp`, `rds_set_ta`, `rds_set_ms_music`, `rds_set_di`, and `rds_set_deviation`. | All parameters appear in the RDS PS tab with validation helpers. | The live metrics banner mirrors PI/PTY/flag state reported by the watchdog loop.【F:si4713/__init__.py†L198-L250】【F:webapp/transmitter.py†L262-L299】【F:webapp/templates/index.html†L186-L233】【F:webapp/transmitter.py†L830-L939】 |
| Program Service (PS) | Eight-slot PS strings, rotation count, and scroll speed via `rds_set_ps` and `rds_set_pscount`. | UI provides slot editors, centering toggle, count, and speed controls; metrics chip list reflects the active rotation. | Rotation updates use `_ps_pairs` and `_maybe_rotate_ps` so displayed chips stay in sync with the transmitted slots.【F:si4713/__init__.py†L258-L276】【F:webapp/transmitter.py†L393-L929】【F:webapp/templates/index.html†L234-L418】 |
| Radiotext (RT) | 32-character RT payloads with A/B management via `rds_set_rt` and `set_rt_ab_mode`. | Core RT editor, rotation playlist, and automation tab expose text, lists, AB mode, repeats, gap, bank, file ingestion, and skip-word filters. | UI inputs cover all parameters consumed by `_burst_rt`, `_resolve_rotation_rt`, and `_resolve_file_rt`.【F:si4713/__init__.py†L278-L374】【F:picast4713.py†L331-L418】【F:webapp/templates/index.html†L344-L471】 |
| Alternative Frequencies | `rds_set_af` helper prepares AF list entries. | Not surfaced anywhere; no config field or UI control uses the method. | Implementing AF would require adding storage and calling `rds_set_af` from the manager pipeline.【F:si4713/__init__.py†L252-L256】【F:picast4713.py†L357-L418】 |
| Telemetry & watchdog | TX status, ASQ input level, over-mod detection via `tx_status` and `read_asq`. | Overview tab shows frequency, power, audio level, RDS identity, watchdog status, and broadcast toggle; watchdog loop publishes the metrics stream. | Displayed metrics come directly from the hardware pollers before being broadcast to SSE clients.【F:si4713/__init__.py†L333-L378】【F:webapp/transmitter.py†L820-L939】【F:webapp/static/js/main.js†L1-L199】【F:webapp/templates/index.html†L60-L136】 |

## Findings

1. **Most front-end controls correspond one-to-one with existing SI4713 driver hooks.** RF tuning, audio dynamics, RDS identification, PS management, RT authoring, automation, and watchdog configuration all map to concrete driver calls or manager settings, so the UI reflects the active hardware capabilities listed above.
2. **Composite, pilot, and baseband options are fixed.** Although the chip allows changing MPX composition, pilot tone, audio deviation, and pre-emphasis, PiCast hardcodes those values in `apply_config`. Operators who need mono-only MPX, different pilot deviation, or 75 µs pre-emphasis cannot adjust them without code changes.
3. **Alternative Frequencies (AF) remain unused.** The driver includes `rds_set_af`, but neither the CLI nor the dashboard accepts AF inputs, so transmitters that should advertise backup frequencies lack that data today.
4. **Displayed metrics originate from live hardware polling.** The overview card surfaces PI, PTY, frequency, power, audio level, and watchdog state directly from the watchdog loop's `tx_status`/`read_asq` sampling, so operators see real readings rather than cached config values.

## Recommendations

- Add optional controls for AF lists if redundancy is required in deployments where receivers expect AF tables.
- Consider exposing advanced RF/audio parameters (MPX enable, pilot deviation, audio deviation, pre-emphasis) behind an "Advanced" disclosure so power users can fully exercise the SI4713 feature set while keeping defaults for typical operators.
- Document the fixed composite defaults in the user-facing README to avoid confusion when comparing the dashboard to the raw SI4713 datasheet.
