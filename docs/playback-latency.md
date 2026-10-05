# Playback latency

Measure the actual invocation-to-playback path with a short muted clip:

```bash
.venv/bin/python scripts/benchmark-playback.py /path/to/video.mkv
.venv/bin/python scripts/benchmark-playback.py --seconds 30 /path/to/video.mkv
```

This developer tool passes temporary mpv options through the internal playback
entry point; the public command still takes only one movie path. It uses a
private temporary IPC socket, tracks mpv launch and first `playback-restart`,
collects output/decoder drop counters, and waits for normal desktop restoration.
Readiness is an IPC measurement, not an optical first-frame measurement.

## Prior real-device measurements

Measured 2026-10-05 on Fedora KDE Wayland / NVIDIA / LG, from the SDR desktop at
3840×2160, 119.88 Hz, with a local 2160p HDR Star Trek episode. No global mpv
configuration or refresh rate was changed. All runs completed desktop cleanup.

| Four-second muted clip | mpv launch | Playback readiness | Total including cleanup |
| --- | ---: | ---: | ---: |
| Original wrapper | 12.49 s | 13.37 s | 28.72 s |
| Optimized menu-free wrapper, before cleanup | 4.73 s | 5.51 s | 13.90 s |

These are individual samples, not statistical distributions or cold-cache
benchmarks. A prior 30-second sample reached readiness at 5.62s with zero
decoder/output frame drops; this does not establish long-term stability.

The deleted scripted UI verification took 10.38s to readiness, and exact UI
state capture took 85.27s after a navigation timeout/retry. Those paths and
their public flags are gone, not just disabled. Visual maintenance is now an
explicit Codex task outside normal playback.

Startup overlaps TV connection, display capture and one HDR metadata probe.
TV writes remain ordered, with conservative one-second settling waits after
each hidden HDMI batch. Picture-mode and TruMotion changes are skipped when
already satisfied. No screenshot, debug snapshot or fixed UI-button route is
part of playback. The installed EDID and non-PC input classification remain
essential to this workstation's movie processing.
