# HDR 119 Hz ALLM/VRR EDID Override

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

Then capture the Game Optimizer dashboard with:

```bash
lg-tv-ui-probe --out-dir /tmp/lg-tv-dashboard --launch-app com.webos.app.gameoptimizer --delay 3.0
```

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
