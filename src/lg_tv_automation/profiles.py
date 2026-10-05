"""The workstation's movie profile and post-playback desktop profile."""

from .constants import (
    DEFAULT_DESKTOP_ICON, DEFAULT_DESKTOP_LABEL, DEFAULT_DESKTOP_PICTURE_MODE,
    DEFAULT_HDR_PICTURE_MODE, DEFAULT_MOVIE_ICON, DEFAULT_MOVIE_LABEL,
    DEFAULT_SDR_PICTURE_MODE, DEFAULT_TRUMOTION,
)
from .models import HdmiFeatureState, TvProfile


def build_movie_profile(*, want_hdr: bool) -> TvProfile:
    return TvProfile(
        label=DEFAULT_MOVIE_LABEL,
        icon=DEFAULT_MOVIE_ICON,
        picture_mode=DEFAULT_HDR_PICTURE_MODE if want_hdr else DEFAULT_SDR_PICTURE_MODE,
        tru_motion=DEFAULT_TRUMOTION,
        hdmi_features=HdmiFeatureState(False, False, False, False),
    )


def build_desktop_profile() -> TvProfile:
    return TvProfile(
        label=DEFAULT_DESKTOP_LABEL,
        icon=DEFAULT_DESKTOP_ICON,
        picture_mode=DEFAULT_DESKTOP_PICTURE_MODE,
        tru_motion=None,
        hdmi_features=HdmiFeatureState(True, True, True, True),
    )
