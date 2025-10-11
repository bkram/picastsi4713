# PiCast SI4713 Dashboard Mock Overview

_No live capture available._ This mock layout highlights the primary sections of the dashboard:

```
+-------------------------------------------------------------+
| Header: PiCast SI4713 Dashboard                             |
+-------------------------------------------------------------+
| Live Broadcast Snapshot                                     |
| - PS, RT, Frequency, Power, PI, PTY                         |
| - RDS flag chips & PS rotation badges                       |
| - Watchdog status + Broadcast toggle                        |
+-------------------------------+-----------------------------+
| Configuration Profiles        | RDS Identity                |
| - Profile selector            | - PI code (0x format)       |
| - Refresh / Save / Apply      | - PTY & deviation fields    |
+-------------------------------+-----------------------------+
| RF Settings                   | RDS Flags                   |
| - Frequency / Power / Cap     | - TP / TA / Music toggles   |
|                               | - DI checkbox chips         |
+-------------------------------+-----------------------------+
| Watchdog & Monitoring         | Program Service (PS)        |
| - Health & ASQ toggles        | - Slot list & cycle speed   |
| - Interval / recovery inputs  | - Center toggle             |
+-------------------------------+-----------------------------+
| (left column continues)       | Radiotext (RT)              |
|                               | - Fallback text / speed     |
|                               | - Rotation entries / skip   |
|                               | - Script path & A/B mode    |
+-------------------------------------------------------------+
```

Refer to `AGENTS.md` for instructions on generating updated mock captures when a browser screenshot cannot be produced.
