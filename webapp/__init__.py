from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

try:  # pragma: no cover - optional during unit tests without Flask installed
    from flask import Flask
except ModuleNotFoundError:  # pragma: no cover - tests import package without Flask
    Flask = None  # type: ignore[assignment]

from .transmitter import TransmitterManager

LOGGER = logging.getLogger(__name__)


def create_app(config: dict[str, Any] | None = None) -> "Flask":
    if Flask is None:  # pragma: no cover - surfaced when Flask missing
        raise RuntimeError("Flask must be installed to create the web interface")
    from .routes import bp
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config.setdefault("CONFIG_ROOT", str(Path("cfg")))
    app.config.setdefault("SECRET_KEY", "picast-si4713")

    if config:
        app.config.update(config)

    manager = TransmitterManager(
        config_root=Path(app.config["CONFIG_ROOT"]),
    )
    app.transmitter_manager = manager  # type: ignore[attr-defined]
    app.register_blueprint(bp)

    @app.teardown_appcontext
    def _shutdown(exception: BaseException | None) -> None:  # noqa: ARG001
        try:
            manager.shutdown()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Failed to stop transmitter manager")

    return app
