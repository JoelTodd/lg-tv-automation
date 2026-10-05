"""Runtime configuration loaded from the user's environment or config directory."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

TV_IP_ENV_VAR = "LG_TV_IP"
TV_IP_CONFIG_DIR = "lg-tv-automation"
TV_IP_CONFIG_FILENAME = "tv-ip"


def pairing_key_path() -> Path:
    """Keep pairing independent of the invoking working directory.

    Preserve the existing library database on first use. SQLite's backup API
    includes committed WAL data and does not expose keys to console output.
    """
    directory = tv_ip_config_path().parent
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / "pairing.sqlite"
    if not target.exists():
        target.touch(mode=0o600)
        for source in (Path.cwd() / ".aiopylgtv.sqlite", Path.home() / ".aiopylgtv.sqlite"):
            if source.is_file():
                try:
                    with sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True) as old_db:
                        with sqlite3.connect(target) as new_db:
                            old_db.backup(new_db)
                except BaseException:
                    target.unlink(missing_ok=True)
                    raise
                break
    return target


def tv_ip_config_path() -> Path:
    """Return the per-user path used to persist the TV address."""

    config_home = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base_dir = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return base_dir / TV_IP_CONFIG_DIR / TV_IP_CONFIG_FILENAME


def require_tv_ip() -> str:
    """Return the configured TV address or raise a friendly setup error.

    The environment variable is useful for one-off overrides. The user config
    file is the durable default and survives terminal, app, and host restarts.
    """

    tv_ip = os.environ.get(TV_IP_ENV_VAR, "").strip()
    if tv_ip:
        return tv_ip

    config_path = tv_ip_config_path()
    try:
        tv_ip = config_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        tv_ip = ""
    except OSError as err:
        raise RuntimeError(f"Could not read TV address from {config_path}: {err}") from err

    if tv_ip:
        return tv_ip

    raise RuntimeError(
        "TV automation needs a TV IP address. Save it in "
        f"{config_path} (one line, for example 192.0.2.10), or set "
        f"{TV_IP_ENV_VAR} for a temporary override. "
        "Ask Codex to help with workstation setup if needed."
    )
