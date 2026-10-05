# Workstation playback baseline

This checkout serves Fedora KDE Wayland + NVIDIA, one LG OLED on HDMI 1, with
KScreen output `HDMI-A-1` and mpv. Playback intentionally leaves refresh at the
existing desktop rate (normally 3840×2160 at 119.88 Hz). There are no preset-only,
status, refresh-switching, hidden UI-verification or EDID-management commands.
Use Python APIs or ordinary read-only host tools for diagnosis, and the visual
remote transport for UI-only state. Never run a competing maintenance session
while playback owns the shared lock.

## Required movie processing

- HDR videos: Fedora HDR and WCG on; TV `hdrFilmMaker`.
- SDR videos: Fedora HDR and WCG off; TV `filmMaker`.
- Input classification: `Blu-ray Player` / `bluray` during playback. This is
  functional, not cosmetic: a generic HDMI/PC classification kept Low Latency
  ON despite hidden off-writes and the EDID workaround on this firmware.
- Cinematic Movement is firmware enum `cinemaClear`, not `cinematicMovement`.
- Hidden 4:4:4, VRR, ALLM and Game Optimizer writes are ordered and reapplied
  after picture changes. Acknowledgments don't prove visible toggle state.
  Check the actual dashboard with screenshots when investigating a failure.
- Desktop cleanup: HDR/WCG off, original mode and VRR policy, HDMI 1 label/icon,
  `expert1` and desktop HDMI flags enabled. Early display failures instead
  restore the exact original display snapshot.

## Accepted EDID workaround

The already-installed kernel override removes HDMI Forum ALLM/VRR advertising
while preserving HDR static metadata, colorimetry, BT.2020 and timings. Do not
disable WCG to work around low latency: it degrades HDR presentation.

Expected boot argument:
`drm.edid_firmware=HDMI-A-1:edid/lg-tv-sscr2-no-allm-vrr.bin`.
Installed firmware is `/usr/lib/firmware/edid/lg-tv-sscr2-no-allm-vrr.bin`.
Read `/proc/cmdline`, `grubby --info=ALL` and the matching
`/sys/class/drm/card*-HDMI-A-1/edid` to check configuration without changing it.
KScreen should report VRR incapable / no configurable policy with this EDID.

Recovery blobs, relative to this skill directory:

| Asset | SHA-256 |
| --- | --- |
| `assets/edid/lg-tv-sscr2-no-allm-vrr.bin` | `556231a8f459cecc687a28e165063698017cf8aca6cd7ed0101a7f330ee676d3` |
| `assets/edid/lg-tv-sscr2-original.bin` | `c5bd1e2b1edc8d7dabc70d87bf481fc9ba680056101241e97d0307b376077426` |

Host EDID/boot repair requires the user's explicit request. Before any such
change, inspect the live connector, each kernel's existing arguments, firmware
contents and ownership; preserve unrelated overrides and create recoverable
backups. Firmware/kernel changes need an initramfs rebuild and usually reboot.
Do not resurrect blind install/remove scripts or change boot state during
routine playback diagnosis.

## Transition failures

KScreen can report an enabled output while the TV says No Signal after HDR
transitions. Playback verifies `hdmiSignalExist` when the TV exposes it, and
does not launch mpv after a failed live-signal check. API picture-mode/TruMotion
failures are logged as degraded playback; input/relabel/hidden-write/signal
failures stop playback. Every cleanup failure produces a nonzero exit status.

On this single-output NVIDIA/Wayland stack, topology glitches can also strand
existing desktop app windows. Diagnose separately; the wrapper does not restart
Plasma or unrelated apps. Restarting an affected app may be appropriate after
the output is stable, but is not an automatic playback action.

TV address: `$XDG_CONFIG_HOME/lg-tv-automation/tv-ip` (normally
`~/.config/lg-tv-automation/tv-ip`), with `LG_TV_IP` as temporary override.
Pairing: the adjacent private `pairing.sqlite`; preserve it. If first connection
times out, check the TV for a pairing prompt. Do not print pairing keys.

### Signal-loss report during source cleanup, 2026-10-05

The user reported an HDMI dropout requiring unplug/replug while only source
edits and hardware-free tests were being performed; no TV/profile/display
writes or playback test were running. Journals showed HDMI audio disappearing
at 22:57:04 BST and Plasma reporting no outputs at 22:57:46 (possibly the
physical disconnect). No NVIDIA crash was logged. After reconnect, mode 14
(3840×2160 at 119.88 Hz), HDR/WCG off and the patched live EDID checksum were
confirmed. Cause remains unknown; do not label this a proven cable, driver or
wrapper failure. Live playback retesting was deferred to avoid disturbing the
recovered link.
