# Repository Guidelines

## Mock Screenshot Procedure
If you are unable to capture a live browser screenshot, create a mock representation instead:
1. Export the desired page section's HTML using the existing Flask templates.
2. Render the HTML with placeholder data using a lightweight static renderer (e.g., `weasyprint` or headless browser screenshot service if available).
3. If rendering tools are unavailable, produce a static PNG or SVG diagram that labels the key UI regions (header, sidebar, cards, forms).
4. Save the mock asset under `docs/screenshots/` and reference it from the documentation.
Ensure all mock screenshots clearly indicate they are mockups and not live captures.

## PiCast SI4713 Web Interface Requirements
All changes to the Flask web interface must satisfy the following distilled goals:
- Present a modern, responsive dashboard that is styled with **Pure.css** components. Keep the layout clean and professional, with the live telemetry banner pinned to the top and each RF/RDS/audio function organised into its own card.
- Surface real-time transmitter metrics with a dedicated card that prominently displays the current Program Service (PS) slot and active Radiotext (RT) values, updating live via Server-Sent Events.
- Provide full configuration management in the browser: users must be able to view, edit, save, duplicate, and apply profiles. RDS controls (PI/PTY/flags, PS rotation, RT programmes, A/B mode, etc.) must remain grouped logically while exposing limiter and compressor settings with preset/reset support.
- Implement a watchdog mechanism equivalent to the CLI behaviour that supervises the SI4713 hardware, restarts it when necessary, and exposes its status within the UI.
- Expose a broadcast toggle that controls the transmitter on/off state and reflects the current status clearly in the interface.
- Preserve functional parity with the CLI v1.1 release: every CLI capability should remain accessible through the web experience, including PI hex editing and audio processing controls.
- Store configurations in a structure compatible with the existing project and provide robust validation, security-conscious input handling, and user feedback for success or error cases.
