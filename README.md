# LG TV Automation

`lg-tv-automation` is a small Python repo for one very specific workstation:
Fedora KDE on Wayland with NVIDIA, connected over HDMI to an LG OLED running
webOS, with `mpv` as the playback front end.

The repo exists to make the setup understandable and maintainable, not just
functional. The command surface stays intentionally small:

- `lg-tv-play`: apply movie mode, apply desktop mode, print status, or wrap `mpv`
- `lg-tv-ui-probe`: exploratory screenshot-driven UI automation tool

When a local directory is passed to `lg-tv-play`, it resolves that directory to
the most likely primary media file before both probing and playback. This
avoids `mpv` treating multi-file release folders as ad-hoc playlists.

## Scope

The automation is built around these real constraints:

- Fedora-side HDR/WCG toggling is reliable through `kscreen-doctor`
- `mpv` playback is already stable with the current local configuration
- The LG TV exposes some relevant settings directly and hides others
- The user must always be returned to `HDMI 1` because that is the path through
  which terminal-side troubleshooting remains possible

This repo therefore automates:

- Fedora display HDR/WCG changes
- TV input relabeling between desktop and movie-friendly states
- TV picture mode switching between `expert1`, `filmMaker`, and `hdrFilmMaker`
- `truMotionMode=cinemaClear` for movie mode
- hidden HDMI/Game Optimizer flags:
  - `4:4:4 Pass Through`
  - Game Optimizer master
  - `VRR & G-Sync`
  - `ALLM`

## Repository Layout

```text
lg-tv-automation/
├── README.md
├── pyproject.toml
├── bin/
│   ├── lg-tv-play
│   └── lg-tv-ui-probe
├── docs/
│   ├── handoff-hdr-vrr-allm-investigation-2026-04-09.md
│   └── handoff-kde-discover-output-state.md
├── src/lg_tv_automation/
│   ├── cli/
│   │   ├── play.py
│   │   └── ui_probe.py
│   ├── constants.py
│   ├── console.py
│   ├── display.py
│   ├── media.py
│   ├── models.py
│   ├── process.py
│   ├── profiles.py
│   ├── tv.py
│   └── tv_ui.py
└── tests/
    └── test_helpers.py
```

## Architecture

### 1. Playback wrapper

`src/lg_tv_automation/cli/play.py` is the user-facing entry point. It decides:

- whether the target content should be treated as SDR or HDR
- whether the command is a preset-only operation or a full `mpv` run
- whether playback cleanup should restore the exact captured state or the
  configured desktop preset

### 2. Fedora display control

`src/lg_tv_automation/display.py` owns `kscreen-doctor` interaction. The retry
logic around `kscreen-doctor -j` is important: the display stack can briefly
drop the output during HDR transitions, and immediate failure would strand the
desktop in the wrong state.

That behavior is not only a restore-risk for this tool. On this KDE Wayland +
NVIDIA + single-HDMI stack, some already-running GUI apps can survive a display
topology glitch in a stale state afterward. The repo keeps the display logic
small and explicit, and documents the broader symptom in
`docs/handoff-kde-discover-output-state.md`.

### 3. TV direct control

`src/lg_tv_automation/tv.py` owns the fast path:

- input label/icon changes through `set_device_info`
- picture mode changes through the available picture APIs
- hidden settings writes for `4:4:4 Pass Through`, Game Optimizer, `VRR`, and `ALLM`

The hidden-setting route uses a deliberate workaround:

- create a temporary alert with `system.notifications/createAlert`
- attach a button callback that calls
  `luna://com.webos.settingsservice/setSystemSettings`
- immediately close the alert, which triggers the same callback path

This is significantly faster and more reliable than walking the TV UI.

### 4. TV UI exploration and state probes

`src/lg_tv_automation/tv_ui.py` and `src/lg_tv_automation/cli/ui_probe.py` are
kept on purpose even though direct hidden writes are the preferred playback
control path.

They are useful when:

- status output or exact-state capture needs UI-visible HDMI/Game Optimizer data
- additional undocumented settings need to be discovered
- visual confirmation of a UI-only control path is needed

## Runtime Behaviour

### Movie mode

Movie mode does the following:

- leave refresh rate alone by default
- optionally force `4K60` if `--force-60hz` is explicitly requested
- enable Fedora HDR/WCG for HDR sources and disable them for SDR sources
- keep the TV on `HDMI 1`
- relabel the input to a normal HDMI label instead of `PC`
- disable `4:4:4 Pass Through`, Game Optimizer, `VRR`, and `ALLM`
- set the TV picture mode to:
  - `filmMaker` for SDR
  - `hdrFilmMaker` for HDR
- set `truMotionMode=cinemaClear`

When `lg-tv-play` launches actual playback rather than `--movie-mode`, it also
tries to match the display refresh to the source frame rate unless
`--force-60hz` or the source metadata says to leave the desktop refresh alone.

### Desktop mode

Desktop mode restores the preferred desktop presentation:

- Fedora HDR/WCG disabled
- picture mode `expert1` (`isf Expert`, bright space/daytime)
- `4:4:4 Pass Through` enabled
- Game Optimizer enabled
- `VRR & G-Sync` enabled
- `ALLM` enabled

### Cleanup contract

When `lg-tv-play` launches `mpv`, it must:

1. enter the right movie preset before playback starts
2. launch `mpv`
3. restore desktop mode when `mpv` exits
4. verify cleanup as much as possible
5. return the TV to `HDMI 1`

The code treats that last requirement as hard policy, not best effort.

## Commands

Status:

```bash
lg-tv-play --status
lg-tv-play --status --no-tv-ui
```

`--status` emits a single JSON document containing both `display` and `tv`
keys.

`--status` does not run concurrently with an active playback or preset session.

Manual movie preset:

```bash
lg-tv-play --movie-mode --hdr
lg-tv-play --movie-mode --sdr
lg-tv-play --movie-mode -- /path/to/movie.mkv
```

Manual desktop preset:

```bash
lg-tv-play --desktop-mode
```

Normal playback:

```bash
lg-tv-play -- /path/to/movie.mkv
```

Useful switches:

```bash
lg-tv-play --hdr -- /path/to/movie.mkv
lg-tv-play --sdr -- /path/to/movie.mkv
lg-tv-play --restore-saved-state -- /path/to/movie.mkv
lg-tv-play --force-60hz -- /path/to/movie.mkv
lg-tv-play --no-tv -- /path/to/movie.mkv
lg-tv-play --no-tv-ui -- /path/to/movie.mkv
lg-tv-play --no-display -- /path/to/movie.mkv
```

`--restore-saved-state` requires TV UI capture, so it is incompatible with
`--no-tv-ui`.

`--no-display` is the preferred escape hatch when you only need the TV-side
preset change and do not need Fedora HDR/WCG or mode changes. It avoids the
most fragile part of the stack.

UI exploration:

```bash
lg-tv-ui-probe --out-dir /tmp/lg-probe --status --before
lg-tv-ui-probe --out-dir /tmp/lg-probe --launch-app com.webos.app.gameoptimizer --buttons RIGHT DOWN DOWN
```

## Local Dependencies

System tools expected on the Fedora host:

- `mpv`
- `ffprobe`
- `kscreen-doctor`

Python packages expected in a project-local `.venv`:

- `bscpylgtv`
- `Pillow`
- `pytest`

The checked-in `bin/` launchers resolve the real repo path and execute the
entrypoints from that `.venv` directly. That means symlinks such as
`~/.local/bin/lg-tv-play` still run against the repo-managed environment
instead of falling back to Fedora's system `python3`.

One setup path is:

```bash
cd /home/joel/code/lg-tv-automation
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

If you prefer `uv`, `uv sync --extra dev` also produces the expected `.venv`.

Use the repo through the checked-in launchers or the environment's console
scripts:

```bash
.venv/bin/lg-tv-play --status
.venv/bin/lg-tv-ui-probe --out-dir /tmp/lg-probe --status --before
```

Direct calls to `bin/lg-tv-play` or `bin/lg-tv-ui-probe` use the same
environment.

## Validation

The safe local checks are:

```bash
bash -n \
  /home/joel/code/lg-tv-automation/bin/lg-tv-play \
  /home/joel/code/lg-tv-automation/bin/lg-tv-ui-probe

/home/joel/code/lg-tv-automation/.venv/bin/pytest -q
```

The live hardware checks that matter are:

- `lg-tv-play --status --no-tv-ui`
- `lg-tv-play --desktop-mode`
- `lg-tv-play --movie-mode --sdr`
- `lg-tv-play --movie-mode --hdr`
- `lg-tv-play -- /path/to/local/test-file`

After any exploratory TV action, confirm the final app is still
`com.webos.app.hdmi1`.

## Troubleshooting

### KDE apps look stuck after a display transition

This environment can occasionally leave long-running GUI apps in a stale state
after KScreen output changes. The repo does not try to become a general Plasma
repair tool, so the guidance stays intentionally small:

- prefer `--no-display` when display-side changes are unnecessary
- restart the affected app if it survives the output transition badly
- treat `docs/handoff-kde-discover-output-state.md` as the focused write-up for
  this failure mode and its rationale

## Notes For Future Maintenance

- Prefer direct hidden-setting writes over UI automation whenever possible.
- Keep the UI probe working anyway; it is the best recovery tool when LG changes
  firmware behavior.
- Do not silently remove the HDMI 1 cleanup guarantees. They are part of the
  operating model, not an implementation detail.
- If playback teardown ever stops restoring the desktop correctly, inspect
  `display.py` first. The display stack is more transient than the TV API.
