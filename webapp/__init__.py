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

    prefer_virtual_cfg = app.config.get("USE_VIRTUAL")
    if isinstance(prefer_virtual_cfg, str):
        prefer_virtual = prefer_virtual_cfg.strip().lower() in {"1", "true", "yes", "on"}
    elif prefer_virtual_cfg is None:
        prefer_virtual = None
    else:
        prefer_virtual = bool(prefer_virtual_cfg)

    manager = TransmitterManager(
        config_root=Path(app.config["CONFIG_ROOT"]),
        prefer_virtual=prefer_virtual,
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
