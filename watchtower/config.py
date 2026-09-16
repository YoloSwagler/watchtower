"""Application configuration: CLI args and environment, no config files."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass, field


def _default_data_dir() -> str:
    # Outside the installed package/source tree on purpose, so a future
    # `git pull`/package upgrade that replaces application files never
    # touches user data (bookmarks). See ARCHITECTURE.md, "Saved
    # frequencies (bookmarks)".
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = xdg_data_home if xdg_data_home else os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "watchtower")


@dataclass(frozen=True)
class AppConfig:
    host: str = "127.0.0.1"
    port: int = 8080
    demo: bool = False
    verbose: bool = False
    gpsd_host: str = "127.0.0.1"
    gpsd_port: int = 2947
    data_dir: str = field(default_factory=_default_data_dir)


def load_config(argv: list[str] | None = None) -> AppConfig:
    """Build config from CLI args, falling back to environment variables.

    CLI flags take precedence over environment variables, which take
    precedence over defaults. There is no config file — this is a small
    single-operator field tool, not a multi-tenant service.
    """
    parser = argparse.ArgumentParser(
        prog="watchtower",
        description="Offline-first SDR listening post and field receiver.",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("WATCHTOWER_HOST", "127.0.0.1"),
        help="Bind address (default: 127.0.0.1, localhost only)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("WATCHTOWER_PORT", "8080")),
        help="Bind port (default: 8080)",
    )
    parser.add_argument(
        "--lan",
        action="store_true",
        help="Bind to 0.0.0.0 instead of localhost (explicit opt-in for LAN access)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        default=os.environ.get("WATCHTOWER_DEMO", "").lower() in ("1", "true", "yes"),
        help="Run with simulated SDR and GPS hardware (no RTL-SDR/GPS required)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable debug-level logging",
    )
    parser.add_argument(
        "--gpsd-host",
        default=os.environ.get("WATCHTOWER_GPSD_HOST", "127.0.0.1"),
        help="gpsd host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--gpsd-port",
        type=int,
        default=int(os.environ.get("WATCHTOWER_GPSD_PORT", "2947")),
        help="gpsd port (default: 2947)",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("WATCHTOWER_DATA_DIR", _default_data_dir()),
        help="Directory for user data such as saved frequencies (default: ~/.local/share/watchtower)",
    )
    args = parser.parse_args(argv)

    host = "0.0.0.0" if args.lan else args.host  # noqa: S104 - explicit opt-in

    return AppConfig(
        host=host,
        port=args.port,
        demo=args.demo,
        verbose=args.verbose,
        gpsd_host=args.gpsd_host,
        gpsd_port=args.gpsd_port,
        data_dir=args.data_dir,
    )
