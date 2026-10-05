"""The three state structures used by playback."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HdmiFeatureState:
    passthrough_444: bool
    game_optimizer_master: bool
    vrr: bool
    allm: bool


@dataclass(frozen=True)
class TvProfile:
    label: str
    icon: str
    picture_mode: str
    tru_motion: str | None
    hdmi_features: HdmiFeatureState | None = None


@dataclass(frozen=True)
class DisplaySnapshot:
    mode_id: str
    hdr: bool
    wcg: bool
    vrr_policy: int | None = None
