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
- Present a modern, responsive dashboard styled with a framework such as Bootstrap or Tailwind and keep the layout clean and professional.
- Surface real-time transmitter metrics with a dedicated card that prominently displays the current Program Service (PS) and Radiotext (RT) values, updating live via WebSockets or Server-Sent Events.
- Provide full configuration management in the browser: users must be able to view, edit, save, duplicate, and apply profiles. Group all Radio Data System (RDS) settings together logically.
- Implement a watchdog mechanism equivalent to the CLI behaviour that supervises the SI4713 hardware, restarts it when necessary, and exposes its status within the UI.
- Expose a broadcast toggle that controls the transmitter on/off state and reflects the current status clearly in the interface.
- Preserve functional parity with the CLI v1.1 release: every CLI capability should remain accessible through the web experience.
- Store configurations in a structure compatible with the existing project and provide robust validation, security-conscious input handling, and user feedback for success or error cases.
