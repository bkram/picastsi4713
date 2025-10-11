# SI4713 TX Power Reference

The Silicon Labs *Si4712/13-B1* datasheet documents how FM power levels
are commanded via the `TX_TUNE_POWER` opcode (0x31) and the `TX_POWER`
property (0x2101). Table 14 of the datasheet specifies that:

- The `POWER` parameter is expressed in **dBµV** and accepts integer
  values from **88 to 115** in normal operation.
- Values above 115 dBµV are supported but called out as "high power" in
the application notes because they violate the nominal regulatory
ceiling and may require additional filtering and thermal management.
- The command byte sequence matches what the CLI and dashboard send:
  `[0x31, 0x00, 0x00, POWER, CAP]`, where `POWER` is the requested dBµV
  target and `CAP` configures the tuning capacitor network.

The original CLI forwards the `rf.power` value directly to `set_output`,
allowing advanced users to deliberately select the 116–120 dBµV range if
their deployment can accommodate it. The web interface preserves the
same behaviour so that the transmitter manager issues the same
`TX_TUNE_POWER` command and honours the `rf.power` field exactly as the
CLI would. This keeps the two control surfaces in lockstep while making
the underlying datasheet expectations explicit.
