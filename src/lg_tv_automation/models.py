"""TV profiles and the display state saved for recovery."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TvProfile:
    label: str
    icon: str
    picture_mode: str
    tru_motion: str | None
    desktop_features: bool


@dataclass(frozen=True)
class DisplaySnapshot:
    mode_id: str
    hdr: bool
    wcg: bool
    vrr_policy: int | None = None
