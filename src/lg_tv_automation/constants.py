"""Project-wide defaults and small TV-specific helpers."""

DEFAULT_TV_IP = "192.168.1.134"
DEFAULT_TV_INPUT = "HDMI_1"
DEFAULT_DISPLAY_OUTPUT = "HDMI-A-1"

DEFAULT_MOVIE_ICON = "HDMI_1"
DEFAULT_MOVIE_LABEL = "HDMI 1"
DEFAULT_SDR_PICTURE_MODE = "filmMaker"
DEFAULT_HDR_PICTURE_MODE = "hdrFilmMaker"
DEFAULT_TRUMOTION = "off"

DEFAULT_DESKTOP_ICON = "HDMI_1"
DEFAULT_DESKTOP_LABEL = "HDMI 1"
DEFAULT_DESKTOP_PICTURE_MODE = "expert1"
DEFAULT_DESKTOP_VRR_POLICY = 2

DEFAULT_DESKTOP_444 = True
DEFAULT_DESKTOP_GAME_OPTIMIZER = True
DEFAULT_DESKTOP_VRR = True
DEFAULT_DESKTOP_ALLM = True

DEFAULT_RETURN_APP = "com.webos.app.hdmi1"


def expected_input_app_id(input_id: str) -> str:
    """Translate a webOS input id such as ``HDMI_1`` to the app id webOS uses."""

    return f"com.webos.app.{input_id.lower().replace('_', '')}"
