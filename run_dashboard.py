from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch the PiCast SI4713 Flask dashboard",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("FLASK_RUN_HOST", "0.0.0.0"),
        help="Hostname or IP to bind (default: %(default)s)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("FLASK_RUN_PORT", "5000")),
        help="Port to listen on (default: %(default)s)",
    )
    parser.add_argument(
        "--config-root",
        type=Path,
        default=Path(os.environ.get("CONFIG_ROOT", "cfg")),
        help="Directory that stores configuration profiles",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable Flask debug mode",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    try:
        from webapp import create_app
    except ModuleNotFoundError as exc:  # pragma: no cover - surfaced without Flask installed
        raise SystemExit(
            "Flask must be installed to launch the dashboard. Install dependencies with `pip install Flask`."
        ) from exc

    config: dict[str, Any] = {"CONFIG_ROOT": str(args.config_root)}
    if args.debug:
        config["DEBUG"] = True

    app = create_app(config)
    app.run(host=args.host, port=args.port, debug=args.debug, use_reloader=args.debug)


if __name__ == "__main__":
    main()
