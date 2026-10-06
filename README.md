# LG TV playback

One command for this Fedora KDE Wayland + NVIDIA workstation and its HDMI-connected LG OLED:

```bash
lg-tv-play "/path/to/movie.mkv"
```

A directory also works: the largest local video is selected before probing and
playback. Quote paths containing spaces. Missing files and unreadable video
metadata fail clearly, without starting playback.

The wrapper detects HDR, prepares Fedora HDR/WCG and the TV's movie profile,
starts mpv, then restores the desktop when playback exits. It preserves the
desktop refresh rate, classifies the input as Blu-ray for movie processing,
uses Filmmaker mode and Cinematic Movement, and checks the live HDMI signal.
Startup performs no UI navigation or screenshots. Independent startup reads
overlap, and already-satisfied settings aren't rewritten.

Precise seeks decode the frames leading up to the target instead of skipping
their decoding (`hr-seek-framedrop=no`). This avoids misleading FFmpeg Dolby
Vision RPU warnings during seeking, at the cost of slightly slower precise
seeks. Hardware decoding and normal playback are unaffected.

Ctrl-C and SIGTERM stop/reap mpv before bounded restoration and disconnect.
Partial preparation failures roll back the display; cleanup returns to HDMI 1.
Concurrent playback or Codex maintenance sessions are refused. A hard kill or
lost TV connection cannot guarantee restoration.

## Setup

Requires Python 3.11+, `mpv`, `ffprobe`, and `kscreen-doctor` on PATH:

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
scripts/install-command-links.sh
```

Save the TV address as one line in `~/.config/lg-tv-automation/tv-ip` (or under
`XDG_CONFIG_HOME`). `LG_TV_IP` provides a temporary override. Pairing is stored
privately alongside it, independent of the calling directory.

For a workstation that still has root-owned links to the removed legacy
commands, run `sudo scripts/cleanup-legacy-system-links.sh`. It validates both
links before removing either, leaves playback/firmware untouched, and can be
previewed without sudo using `--dry-run`.

This workstation also requires its existing ALLM/VRR EDID override and Blu-ray
input classification for full HDR at 119.88 Hz. The wrapper doesn't install or
alter firmware/boot settings. Ask Codex to handle setup or repair.

## Maintenance belongs to Codex

The repository's [lg-tv-control skill](.agents/skills/lg-tv-control/SKILL.md)
supports visual TV navigation and has the workstation's recovery notes/assets.
Its private remote transport supplies screenshots and single actions; Codex
chooses and verifies the steps. There are no scripted menu routes or pixel
detectors, and no additional public commands, preset/status modes, or UI flags.

Direct TV API acknowledgments don't prove visible hidden-toggle state. Picture
mode/TruMotion failures are reported as degraded playback; input or live-signal
failures stop playback. Ask Codex for visual diagnosis when necessary.

Tests: `.venv/bin/pytest -q`. For developer latency checks, see
[docs/playback-latency.md](docs/playback-latency.md).

MIT licensed. Local TV configuration, pairing credentials and screenshots must
not be committed.
