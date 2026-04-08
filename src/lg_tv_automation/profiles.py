"""Profile builders for the two user-facing operating modes."""

from __future__ import annotations

from .constants import (
    DEFAULT_DESKTOP_444,
    DEFAULT_DESKTOP_ALLM,
    DEFAULT_DESKTOP_GAME_OPTIMIZER,
    DEFAULT_DESKTOP_VRR,
)
from .models import HdmiFeatureState, TvProfile


def build_movie_profile(
    *,
    want_hdr: bool,
    movie_label: str,
    movie_icon: str,
    sdr_picture_mode: str,
    hdr_picture_mode: str,
    tru_motion: str,
) -> TvProfile:
    """Build the movie preset.

    Movie mode deliberately disables the HDMI features that push the TV toward
    its desktop or gaming processing path.
    """

    return TvProfile(
        label=movie_label,
        icon=movie_icon,
        picture_mode=hdr_picture_mode if want_hdr else sdr_picture_mode,
        tru_motion=tru_motion,
        hdmi_features=HdmiFeatureState(
            passthrough_444=False,
            game_optimizer_master=False,
            vrr=False,
            allm=False,
        ),
    )


def build_desktop_profile(
    *,
    desktop_label: str,
    desktop_icon: str,
    desktop_picture_mode: str,
) -> TvProfile:
    """Build the default desktop preset used after playback."""

    return TvProfile(
        label=desktop_label,
        icon=desktop_icon,
        picture_mode=desktop_picture_mode,
        tru_motion=None,
        hdmi_features=HdmiFeatureState(
            passthrough_444=DEFAULT_DESKTOP_444,
            game_optimizer_master=DEFAULT_DESKTOP_GAME_OPTIMIZER,
            vrr=DEFAULT_DESKTOP_VRR,
            allm=DEFAULT_DESKTOP_ALLM,
        ),
    )
