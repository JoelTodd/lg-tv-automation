# Workstation validation — 2026-10-09

Checked after the workstation's system and application updates.
Further findings and current behavior are recorded in the
[behavior audit](behavior-audit.md).

## Installed stack

- Fedora 44 KDE, kernel `7.2.9-200.fc44.x86_64`.
- NVIDIA RTX 4070 Ti, driver `615.71.09`.
- Plasma/KWin `6.7.5`, mpv `0.41.0`, FFmpeg runtime `8.1.3`.
- Python `3.14.8`; refreshed the existing virtual environment and reinstalled
  the editable project using `constraints-tested.txt`. Dependency checks pass.
- The playback command resolves to this checkout. Obsolete EDID/UI-probe
  command links are already absent.

## Display and TV

The live HDMI-A-1 EDID and installed override both match the accepted checksum
`556231a8f459cecc687a28e165063698017cf8aca6cd7ed0101a7f330ee676d3`.
The running kernel retains the expected override argument. The desktop remains
3840×2160 at 119.88 Hz, with HDR/WCG off and VRR incapable.

Before playback, the TV reported HDMI 1, a live HDMI signal, `expert1`, and the
desktop input label/icon. No boot configuration or firmware changes were needed.

## Short playback checks

Generated local 1920×1080, 24 fps test patterns, with four seconds of muted
playback through the normal preparation/player/restoration workflow and the
developer benchmark. The HDR fixture was verified to expose PQ (`smpte2084`),
BT.2020, and 10-bit HEVC before running the HDR check.

| Clip | mpv launch | Playback readiness | Total with restoration | Decoder/output drops | Exit |
| --- | ---: | ---: | ---: | ---: | ---: |
| SDR H.264 | 4.07 s | 4.73 s | 13.47 s | 0 / 0 | 0 |
| HDR HEVC Main 10 | 4.80 s | 5.53 s | 14.60 s | 0 / 0 | 0 |

Neither run reported degraded picture/motion settings or cleanup failures.
After playback, KScreen again reported connected/enabled HDMI-A-1, mode 14,
HDR/WCG off, and no VRR policy. These short synthetic samples verify startup
and restoration, not sustained 4K playback, audio, Dolby Vision, or optical
confirmation of hidden TV switches. An earlier incorrectly tagged fixture ran
as SDR and is excluded from the HDR result above.

## Maintenance cleanup correction

The user reported being sent to the TV's main menu during an observation-only
maintenance check. That session's cleanup unconditionally sent EXIT, even
though it had opened no overlay. Observation-only sessions now disconnect
without remote keys or input changes. After navigation, cleanup launches HDMI
directly and verifies it, including when an action's response is lost. No blanket
EXIT key is sent. Settings overlays are closed visually before quitting because
launching HDMI alone did not dismiss the Settings overlay in the live check.

Live retesting confirmed observation/quit leaves HDMI 1 intact. A Settings
inspection used one BACK key to close the visible root overlay, then quit
successfully with direct HDMI cleanup. Final TV reads showed a live HDMI signal,
the desktop label/icon, and `expert1`.

Hardware-free validation: 77 tests and 33 subtests pass, including observation
quit/EOF/idle/failure paths, navigation cleanup, cancellation, and shared-lock
contention. Shell syntax and Git whitespace checks pass.

Removed generated caches and empty asset directories left by deleted modules.
Preserved both pairing databases and tightened their local permissions. The
tracked recovery EDID assets remain in the lg-tv-control skill.
