"""Runtime configuration loaded from the user's environment."""

from __future__ import annotations

import os

TV_IP_ENV_VAR = "LG_TV_IP"


def require_tv_ip() -> str:
    """Return the configured TV IP address or raise a friendly setup error."""

    tv_ip = os.environ.get(TV_IP_ENV_VAR, "").strip()
    if tv_ip:
        return tv_ip
    raise RuntimeError(
        "TV automation needs LG_TV_IP. Set it to your TV's IP address, "
        f"for example: export {TV_IP_ENV_VAR}=192.0.2.10. "
        "Use --no-tv for workflows that should skip TV-side automation."
    )
