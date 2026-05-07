# HDR 119.88 Hz ALLM/VRR EDID Override

## Problem

On the target Fedora KDE + NVIDIA + LG OLED setup, full HDR/WCG at
`3840x2160@119.88` caused the LG Game Optimizer dashboard to report `VRR ON`
and `Low Latency ON` even after `lg-tv-play` wrote movie-mode HDMI/Game
Optimizer state.

Disabling WCG cleared the dashboard state during diagnosis, but that degrades
HDR presentation and is not an acceptable mitigation.

## Accepted Mitigation

The accepted mitigation is a kernel EDID firmware override for `HDMI-A-1`:

- packaged patched EDID:
  `src/lg_tv_automation/assets/edid/lg-tv-sscr2-no-allm-vrr.bin`
- original captured EDID:
  `src/lg_tv_automation/assets/edid/lg-tv-sscr2-original.bin`

The patched EDID preserves HDR/WCG presentation:

- HDR Static Metadata Data Block unchanged
- Colorimetry Data Block unchanged
- BT.2020/WCG advertising unchanged
- video/audio/timing data unchanged

It only removes the HDMI Forum ALLM/VRR advertisement:

- HDMI Forum ALLM support bit cleared
- HDMI Forum VRR minimum refresh set to 0
- HDMI Forum VRR maximum refresh set to 0
- CTA checksum recomputed

This is intentionally different from the earlier refresh-rate workaround. The
current desired state is full HDR/WCG at the normal desktop refresh
(`3840x2160@119.88`) with ALLM and VRR inactive. `lg-tv-play --match-refresh`
still exists as an explicit diagnostic or compatibility switch, but it is not
the primary mitigation.

## Input Classification Requirement

As of the May 7, 2026 recheck, the EDID override and hidden ALLM off-writes are
not sufficient by themselves if HDMI 1 remains classified as a generic HDMI/PC
source. The visible label/icon may already say `HDMI 1`, but the LG can still
keep the active Game Optimizer dashboard in `Low Latency ON`.

Manual validation showed this sequence:

- `lg-tv-play --movie-mode --hdr --no-tv-ui` with the old `HDMI 1` /
  `HDMI_1` movie input profile left the Game Optimizer dashboard at
  `Low Latency ON`.
- Reapplying hidden `enableALLM`, `inputOptimization`, `enableQuickGame`, and
  Game Optimizer master off-writes did not clear the dashboard state.
- `lg-tv-play --match-refresh` changed the dashboard to `24 FPS`, but
  `Low Latency` still remained `ON` on this firmware/state.
- Forcing HDMI 1 to the non-PC `Blu-ray Player` / `bluray` input profile made
  the same dashboard report `Low Latency OFF` while preserving HDR/WCG,
  `hdrFilmMaker`, and `4:4:4 Pass Through` off.

For this reason, movie mode now defaults to:

```text
DEFAULT_MOVIE_LABEL = "Blu-ray Player"
DEFAULT_MOVIE_ICON = "bluray"
```

Do not "simplify" movie mode back to `HDMI 1` / `HDMI_1` just because the
visible TV input label already looks neutral. On this setup, `set_device_info`
with the Blu-ray icon is part of clearing the TV's low-latency path.

## Commands

Check status:

```bash
lg-tv-edid status
```

Verify the override is installed and active:

```bash
lg-tv-edid verify
```

Install:

```bash
sudo lg-tv-edid install
sudo systemctl reboot
```

Remove:

```bash
sudo lg-tv-edid remove
sudo systemctl reboot
```

Checkout-local wrappers are also available:

```bash
bin/lg-tv-edid status
bin/lg-tv-edid verify
sudo scripts/install-edid-override.sh
scripts/verify-edid-override.sh
sudo scripts/remove-edid-override.sh
```

Use the checkout-local wrappers when `.venv/bin` is not on `PATH` or when
`sudo` cannot resolve `lg-tv-edid`.

If command links have been installed with `scripts/install-command-links.sh`,
both `~/.local/bin/lg-tv-edid` and `/usr/local/bin/lg-tv-edid` should resolve
to this checkout's `bin/lg-tv-edid` wrapper.

## Expected Good State

After install and reboot:

- `/proc/cmdline` includes:
  `drm.edid_firmware=HDMI-A-1:edid/lg-tv-sscr2-no-allm-vrr.bin`
- `/sys/class/drm/card*-HDMI-A-1/edid` has SHA-256:
  `556231a8f459cecc687a28e165063698017cf8aca6cd7ed0101a7f330ee676d3`
- KScreen reports:
  - `3840x2160@119.88`
  - HDR enabled during movie mode
  - WCG enabled during movie mode
  - VRR incapable / no configurable `vrrPolicy`
- HDMI 1 input metadata reports:
  - label `Blu-ray Player` during movie mode
  - icon `bluray.png` during movie mode
- LG Game Optimizer dashboard reports:
  - `120 FPS`
  - `VRR OFF`
  - `Low Latency OFF`

## Validation

Run:

```bash
lg-tv-play --movie-mode --hdr --no-tv-ui
lg-tv-edid verify
```

Do not use WCG-off as validation. WCG must remain enabled for a full HDR
presentation.

Then capture the Game Optimizer dashboard with:

```bash
lg-tv-ui-probe --out-dir /tmp/lg-tv-dashboard --launch-app com.webos.app.gameoptimizer --delay 3.0
```

For a manual end-to-end validation of the current target file, run:

```bash
lg-tv-play "/home/joel/Downloads/For.All.Mankind.S01.HDR.2160p.WEB.h265-PETFRiFiED[rartv]/For.All.Mankind.S01E10.HDR.2160p.WEB.h265-PETFRiFiED.mkv"
```

While playback is active, capture:

```bash
lg-tv-ui-probe --out-dir /tmp/lg-tv-dashboard-playback --launch-app com.webos.app.gameoptimizer --delay 3.0 --status
```

The dashboard should show `VRR OFF` and `Low Latency OFF`. The saved
`status.json` should show HDMI 1 as `Blu-ray Player` with `bluray.png`.

The decisive historical validation evidence lives outside this repo in:

```text
/home/joel/Documents/system-troubleshooting/2026-04-16-mpv-dropped-frames/evidence/P4
```

Use the troubleshooting artifact guide for exact screenshot/log locations:

```text
docs/troubleshooting-artifacts.md
```

## Recovery Notes

If the display behaves badly after installing the override, remove it and
reboot:

```bash
sudo lg-tv-edid remove
sudo systemctl reboot
```

Removal restores the TV-provided EDID on the next boot. It does not modify
TV-side picture mode, ALLM, VRR, or Game Optimizer settings.
