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
overlap after the media probe succeeds. Already-satisfied picture/display
settings aren't rewritten; hidden HDMI writes remain necessary because their
visible state cannot be reliably read through the API.

In an interactive terminal, a dashboard fills an 80×24 window and adapts to
resizing. It shows playback position, video size/frame rate and HDR/SDR, display
refresh, setup time, and an audio spectrum. A short shutter reveal opens the
screen, with a consistent violet border. The timeline is cyan up to the playhead
and muted grey for the remaining time. Setup/restoration show their actual steps. After
restoration, a brief closing shutter gives way to the session log; failures and
interruptions skip this final animation. `NO_COLOR` disables colour. Small
terminals use a compact layout.

The spectrum uses optional `pactl` and `parec` tools (Fedora's
`pulseaudio-utils`) to monitor only this mpv process's output stream through
PulseAudio or PipeWire's PulseAudio compatibility service. No microphone,
playback filter, or audio-routing changes are used, and no audio is saved.
Twenty-four logarithmic bands cover 50 Hz–10 kHz on a fixed −70 to 0 dBFS scale.
Overlapping audio windows provide about 47 measurements per second; the dashboard
redraws at up to 30 frames per second.
Missing tools, unsupported audio outputs/passthrough, or an absent audio track
leave the spectrum unavailable without blocking playback.

A single alert line surfaces errors or abnormal playback conditions; full
details go to the exit log. Routine A/V values, zero-drop counters, fixed profile
settings, and successful checks are omitted from the dashboard. mpv's terminal
shortcuts remain available. Telemetry doesn't poll the TV or display.

After exit, a spaced session report remains in terminal scrollback, grouped into
timing, media, and diagnostics. It records the result, last position, aggregate
setup/playback/restore timings, and any warnings or errors. Nonzero frame totals
account for counter resets; large A/V offsets are reported as exceptions.
Failures also include individual step timings. Repeated diagnostics are counted;
the latest 200 entries and latest error are retained, with omissions noted.
Redirected stderr and `TERM=dumb` use plain logs and a timing/exit summary.

Playback explicitly uses NVIDIA's `nvdec` decoder. Automatic selection chose
Vulkan Video after the update, and repeated SDR tests stalled during player
shutdown. The playback-only option preserves Vulkan rendering and HDR; it
doesn't change the user's global mpv configuration.

Precise seeks decode the frames leading up to the target instead of skipping
their decoding (`hr-seek-framedrop=no`). This avoids misleading FFmpeg Dolby
Vision RPU warnings during seeking, at the cost of slightly slower precise
seeks. Hardware decoding and normal playback are unaffected.

Ctrl-C and SIGTERM stop/reap mpv before bounded restoration and disconnect.
Partial preparation failures roll back the display. Cleanup returns to HDMI 1
after TV preparation has started; earlier failures leave the TV input untouched.
Concurrent playback or Codex maintenance sessions are refused. A hard kill or
lost TV connection cannot guarantee restoration.

## Setup

Requires Python 3.11+, `mpv`, `ffprobe`, and `kscreen-doctor` on PATH:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -c constraints-tested.txt -e '.[dev]'
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

Direct TV API acknowledgments don't prove visible hidden-toggle state. Earlier
picture-mode/TruMotion failures are reported as degraded playback; input,
live-signal or final profile verification failures stop playback. The completed
profile's picture mode is checked again, with one repair if needed. Ask Codex
for visual diagnosis when necessary.

Tests: `.venv/bin/pytest -q`. For developer latency checks, see
[docs/playback-latency.md](docs/playback-latency.md).

After a system Python update, refresh the existing environment and reinstall
the project with its tested dependency versions:

```bash
python3 -m venv --upgrade .venv
.venv/bin/python -m pip install -c constraints-tested.txt -e '.[dev]'
.venv/bin/python -m pip check
.venv/bin/pytest -q
```

Workstation validation and remaining limits are recorded in
[docs/workstation-validation.md](docs/workstation-validation.md).
The detailed [behavior audit](docs/behavior-audit.md) records side-effect fixes,
retained safeguards and longer playback/cancellation checks.

MIT licensed. Local TV configuration, pairing credentials and screenshots must
not be committed.
