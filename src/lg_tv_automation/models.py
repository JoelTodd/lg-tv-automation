"""Small data structures shared across modules."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HdmiFeatureState:
    """TV-side HDMI and Game Optimizer flags that affect movie processing."""

    passthrough_444: bool
    game_optimizer_master: bool
    vrr: bool
    allm: bool

    def as_dict(self) -> dict[str, bool]:
        """Return the flags in the same shape exposed by ``--status``."""

        return {
            "passthrough_444": self.passthrough_444,
            "game_optimizer_master": self.game_optimizer_master,
            "vrr": self.vrr,
            "allm": self.allm,
        }


@dataclass(frozen=True)
class TvProfile:
    """Named TV preset that can be applied directly or restored later."""

    label: str
    icon: str
    picture_mode: str
    tru_motion: str | None
    hdmi_features: HdmiFeatureState | None = None


@dataclass(frozen=True)
class DisplaySnapshot:
    """Relevant Fedora-side output state that should survive playback."""

    mode_id: str
    hdr: bool
    wcg: bool
    vrr_policy: Any = None


@dataclass(frozen=True)
class SavedTvState:
    """Captured TV state from the active HDMI input."""

    current_app: str
    label: str
    icon: str
    picture_mode: str
    app_id: str
    hdmi_features: HdmiFeatureState | None = None
    exact: bool = True
    hdmi_signal_exists: bool | None = None

    def as_profile(self) -> TvProfile:
        """Convert the captured state into a re-applicable profile."""

        return TvProfile(
            label=self.label,
            icon=self.icon,
            picture_mode=self.picture_mode,
            tru_motion=None,
            hdmi_features=self.hdmi_features,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly form used by ``--status`` and debugging."""

        payload: dict[str, Any] = {
            "current_app": self.current_app,
            "label": self.label,
            "icon": self.icon,
            "picture_mode": self.picture_mode,
            "app_id": self.app_id,
            "exact": self.exact,
        }
        if self.hdmi_signal_exists is not None:
            payload["hdmi_signal_exists"] = self.hdmi_signal_exists
        if self.hdmi_features is not None:
            payload.update(self.hdmi_features.as_dict())
        return payload
