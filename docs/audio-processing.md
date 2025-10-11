# SI4713 Audio Processing Reference

## Why the SI4713 Limits at –16 dBFS

The SI4713’s digital audio pipeline reserves headroom for the 19 kHz pilot
and the 57 kHz RDS sub-carrier before the composite (MPX) signal reaches the
output stage. Silicon Labs’ limiter application guidance describes the
limiter attack level as a fixed –16 dBFS threshold so that 0 dBFS remains
available for the pilot, sub-carriers, and any momentary overshoot. Feeding
audio hotter than –16 dBFS therefore forces the limiter to act
continuously, which is observed as “clipping” in the dashboard telemetry.
Keeping program audio below that threshold preserves 75 kHz deviation
headroom for stereo and RDS payloads while avoiding harsh limiting.

## Recommended Presets

The dashboard’s preset selector mirrors the application note values so you
can quickly recover a stable baseline after experimenting with manual
settings. The table below summarises the presets and the register values the
web interface applies when you click **Reset to preset**.

| Preset | AGC | Limiter | Comp Threshold (dB) | Attack Index | Release Index | Gain (dB) | Limiter Release (index) | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Broadcast reference (–16 dBFS) | On | On | –30 | 0 | 2 | 15 | 50 | Matches the CLI defaults and Silicon Labs’ “balanced broadcast” example. |
| Music – Smooth | On | On | –24 | 2 | 8 | 12 | 80 | Softer threshold and longer release for wide dynamic music programming. |
| Speech – Articulate | On | On | –20 | 1 | 4 | 10 | 40 | Faster recovery tailored for spoken-word content without pumping. |

You can still enter custom values—the preset selector simply reflects the
closest match based on the active form fields and disables the reset button
when the form diverges from a known combination.

## Reset Workflow in the Dashboard

1. Pick the desired preset from the drop-down (for example “Music – Smooth”).
2. Click **Reset to preset**. The limiter and compressor fields are repopulated
   with the corresponding register values, and the dashboard marks the
   configuration as dirty so you can save or apply it.
3. Adjust the input gain feeding the SI4713 so the live audio meter hovers a
   few dB below –16 dBFS. This keeps the limiter from clamping constantly
   while still protecting against unexpected peaks.

Refer back to this document whenever you need to justify the limiter
behaviour or explain the preset values to collaborators migrating from the
CLI workflow.
