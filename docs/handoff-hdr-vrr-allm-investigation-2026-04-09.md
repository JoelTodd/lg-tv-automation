# Handoff: HDR Playback, VRR/ALLM, and LG Movie-Mode Investigation

## Scope

This report covers the full debugging and implementation work performed after
commit `c9c9639` (`Refactor LG TV automation into a documented repo`) during
the April 9, 2026 session.

The user-reported symptom was:

- `lg-tv-play` could switch the TV into the correct HDR picture mode for an HDR
  MKV.
- HDR and 4:4:4 Pass Through generally behaved as expected.
- VRR and ALLM appeared to stay on during movie playback, which is undesirable
  for the intended "movie mode" UX.

The user also explicitly constrained the design:

- UI fallback is allowed only for status checking and development validation.
- UI fallback should not be part of the main playback UX flow.
- The agent should do as much live testing as possible instead of pushing
  end-user validation back to the user.

## Current Working Theory

The strongest conclusion reached in this run is:

1. There were multiple real bugs in the codebase, and they were fixed.
2. The visible "VRR ON / Low Latency ON" behavior during playback was not
   caused only by missed TV-side writes.
3. The most important on-device differentiator was the active HDMI refresh rate
   during playback:
   - 4K120 HDR: TV showed active VRR and Low Latency on.
   - 4K60 HDR: TV still showed active VRR and Low Latency on.
   - 4K24 HDR: TV showed active VRR off and Low Latency off.
4. The code now auto-matches playback refresh to the source video frame rate,
   which is the most important fix from this run.

One caveat remains:

- On this firmware, the detailed Game Optimizer / ALLM preference row can still
  appear enabled even when the top-level dashboard indicators are off during 24
  Hz playback. No proven hidden setting write was found in this run that
  clears that detailed row reliably.

## Files Changed In This Run

Tracked modifications after `c9c9639`:

- `src/lg_tv_automation/cli/play.py`
- `src/lg_tv_automation/display.py`
- `src/lg_tv_automation/media.py`
- `src/lg_tv_automation/tv.py`
- `src/lg_tv_automation/tv_ui.py`
- `tests/test_helpers.py`

Untracked session artifacts:

- `.aiopylgtv.sqlite`
- `tv-pics-test/`

## Investigation Timeline

### 1. Initial HDR Misclassification

Original user symptom:

- Passing a directory to `lg-tv-play` caused HDR detection to fail.
- The launcher treated the content as SDR because `ffprobe` was run on the
  directory path instead of the actual `.mkv` file.

What changed:

- `media.py` gained directory-aware probing via `resolve_probe_path()`.
- HDR detection now resolves a directory input to the most likely primary media
  file before running `ffprobe`.

Why it mattered:

- This was the reason the TV initially switched to SDR movie mode even though
  the file itself was HDR.

Result:

- HDR classification was fixed.
- Running the same command afterward correctly selected HDR movie mode.

### 2. Cleanup / Restore Failures On Python 3.14

Observed behavior:

- Playback runs could end with a traceback during restore.
- `asyncio.CancelledError` interrupted cleanup inside `finally`.
- The TV could be left in movie mode or an incomplete restored state.

Root cause:

- Python 3.14 treats `CancelledError` as a `BaseException`, so ordinary cleanup
  assumptions were no longer safe.

What changed:

- `cli/play.py` gained `run_cleanup_step()` to temporarily defer cancellation
  while cleanup steps finish.
- Restore/disconnect paths were wrapped so TV restore, desktop preset restore,
  and disconnect are retried instead of failing immediately.
- `main()` now exits with code `130` on interrupted runs instead of surfacing a
  traceback.

Why it mattered:

- This fixed a real state-management bug unrelated to VRR/ALLM semantics.

### 3. Main-Flow UI Fallback Was Removed

Observed behavior:

- Earlier implementations still had the possibility of using screenshot-driven
  UI control for HDMI/Game Optimizer changes during the main profile-apply
  path.

User requirement:

- UI fallback must not be used in the main UX flow.

What changed:

- `tv.py` was tightened so `apply_profile()` only uses the direct hidden-setting
  path for HDMI/Game Optimizer state changes.
- UI automation remains available for status/dev validation only.

Why it mattered:

- This aligned the runtime behavior with the product constraint.

### 4. Hidden LG Writes Were Expanded

Observed behavior:

- Direct writes for VRR/ALLM looked successful in logs, but visible behavior on
  the TV still suggested incomplete disabling.

What changed in `tv.py`:

- VRR writes now target both:
  - `gameOptimization`
  - `gameOptimizationHDMI1`
- ALLM / game-latency-related writes now target:
  - `enableALLM`
  - `inputOptimization`
  - `enableQuickGame`
- Game Optimizer master uses:
  - `gameMode: {"hdmi1": "on"|"off"}`
- 4:4:4 Pass Through uses:
  - `444BypassHDMI1`

Order of operations was also made explicit:

- When disabling movie-hostile features, VRR and ALLM-related writes are sent
  before disabling Game Optimizer master.
- When enabling desktop mode, Game Optimizer is brought up first, then the
  dependent settings are enabled.

Why it mattered:

- This was a necessary improvement even though it did not fully resolve the
  visible playback symptom by itself.

### 5. TV RPC Hangs And Pairing Prompt Failure Modes

Observed behavior:

- `lg-tv-play` could hang after printing `Setting VRR & G-Sync to off.`
- Later runs also hung on other TV RPCs such as TruMotion changes.
- The user observed a TV prompt asking permission for a mobile device to
  connect.

Root cause:

- Several TV-side requests were unbounded and could wait forever.
- Pairing/authorization state could stall connection or hidden writes.

What changed:

- `tv.py` gained request timeouts:
  - `TV_CONNECT_TIMEOUT = 8.0`
  - `TV_REQUEST_TIMEOUT = 5.0`
  - `TV_HIDDEN_SETTINGS_TIMEOUT = 3.0`
- `connect()`, `get_current_app()`, input relabel, picture-mode writes,
  hidden-setting alert calls, and TruMotion writes now all go through
  `_request_with_timeout()`.
- Connection timeout errors now explicitly tell the operator to check for a TV
  pairing prompt.

Why it mattered:

- This eliminated indefinite hangs and turned them into diagnosable failures.

### 6. Wayland `content-type` Experiment

Observed behavior:

- There was a plausible theory that mpv / Wayland / NVIDIA presentation hints
  were causing the TV to re-enter game-style behavior during playback.

What changed:

- `media.normalize_mpv_args()` now injects
  `--wayland-content-type=none` by default on Wayland, unless the user already
  provided an explicit `--wayland-content-type=...` override.

Outcome:

- This did not solve the final problem by itself.
- It remains in place because it was a reasonable, low-cost mitigation and
  preserved explicit user overrides.

Important note:

- Do not treat this as the primary fix for the VRR/ALLM issue.
- The stronger on-device evidence later pointed elsewhere.

### 7. Fedora-Side VRR Policy Was Previously Captured But Not Applied

Observed behavior:

- Earlier investigation showed that display snapshots contained `vrrPolicy`,
  but the movie-mode path was not actively forcing `vrrpolicy.never`.

What changed in `display.py`:

- `DisplayController.capture()` now persists `vrr_policy`.
- `apply_movie_state()` always applies:
  - `output.<name>.vrrpolicy.never`
- `apply_desktop_state()` restores the saved VRR policy when known.
- `restore()` also restores the saved VRR policy when known.
- `_wait_for_state()` now verifies `vrrPolicy` in addition to mode/HDR/WCG.

Why it mattered:

- This fixed a real host-side bug.
- However, later evidence showed host-side correctness alone was not enough to
  satisfy the visible TV behavior during playback.

### 8. Debug Logging And Playback Cue Support

Problem:

- The remaining mismatch was between what the host/display side reported and
  what the TV UI visibly showed during active playback.

What changed in `cli/play.py`:

- Added `--debug-log <path>` for JSONL runtime snapshots.
- Added `--debug-cue-seconds <n>` so the tool can emit a deterministic
  `DEBUG CUE: Take the TV picture now.`
- Added runtime state capture helpers:
  - `snapshot_display_state()`
  - `snapshot_runtime_state()`
  - `append_debug_event()`
  - `run_mpv_with_debug()`

Debug JSONL events emitted:

- `start`
- `after_apply`
- `in_playback`
- `after_tv_restore`
- `post_restore`

Why it mattered:

- This enabled synchronized user-supplied TV photos and machine-side logs from
  the same playback window.

### 9. User-Supplied Ground Truth Proved The Host Side Was Correct

Artifacts:

- `tv-pics-test/01/lg-tv-debug.jsonl`
- `tv-pics-test/01/IMG_4096.jpeg`
- `tv-pics-test/01/IMG_4097.jpeg`

What those artifacts showed:

- The machine-side playback state at `2026-04-09T08:02:02+01:00` was:
  - `hdr=true`
  - `wcg=true`
  - `vrrPolicy=0`
  - TV picture mode `hdrFilmMaker`
- The TV photos taken during that playback window still showed:
  - top dashboard: `VRR ON`, `Low Latency ON`
  - detailed Game Optimizer / ALLM view: ALLM on, Game Optimizer master on

Why it mattered:

- This ruled out the simplistic explanation that the host side was still wrong.
- It also proved the earlier "the problem no longer reproduces" conclusion was
  not trustworthy enough.

### 10. UI Reader Calibration

Problem:

- The screenshot-driven UI probes were built against assumptions that did not
  cleanly match this firmware/layout.

What changed:

- `tv_ui.py` had its master-switch detection threshold lowered:
  - from `> 900`
  - to `> 650`

Artifacts collected for calibration:

- `tv-pics-test/02/ui-nav2/`
- `tv-pics-test/02/ui-capture/`

Why it mattered:

- This improved dev-only UI interpretation, but it still was not trusted as the
  final authority because opening the TV UI can perturb state.

### 11. Reapplying Hidden Off-Writes During Playback Did Not Fix The Symptom

Live dev investigation:

- A dedicated script applied movie mode, launched mpv, captured TV screenshots
  during playback, then re-applied the hidden "off" HDMI/Game Optimizer writes
  while playback was active.

Artifacts:

- `tv-pics-test/live-investigation/before-reapply/`
- `tv-pics-test/live-investigation/after-reapply/`

Key finding:

- Reapplying the exact VRR/ALLM/Game Optimizer hidden writes during active
  playback did not change the visible TV state.

Visible evidence:

- In both `before-reapply/13_down10.jpg` and `after-reapply/13_down10.jpg`,
  the TV still showed the game-style behavior during playback.

Why it mattered:

- This strongly suggested the hidden writes were not the decisive factor once
  playback was already running.

### 12. Refresh-Rate Investigation Identified The Strongest Causal Signal

Live experiments:

- Playback was tested while forcing different active HDMI refresh rates.

Artifacts:

- `tv-pics-test/live-force60/`
- `tv-pics-test/live-force24/`

Observed results:

- 4K120 HDR:
  - active dashboard showed `VRR ON`
  - active dashboard showed `Low Latency ON`
- 4K60 HDR:
  - active dashboard still showed `VRR ON`
  - active dashboard still showed `Low Latency ON`
- 4K24 HDR:
  - active dashboard showed `24 FPS`
  - active dashboard showed `VRR OFF`
  - active dashboard showed `Low Latency OFF`

Most important artifact:

- `tv-pics-test/live-force24/13_down10.jpg`

Why it mattered:

- This was the strongest on-device evidence collected in the session.
- It showed that matching playback to 24 Hz disabled the active game-style
  behavior, even though some deeper settings UI still looked sticky.

### 13. Auto-Matching Display Refresh To Source FPS

What changed in `media.py`:

- Added `detect_video_frame_rate()` using `ffprobe`.
- Added `_parse_frame_rate()` for values like `24000/1001`.
- Added `choose_display_refresh_rate()` to derive a playback-friendly display
  refresh from the source media.

What changed in `display.py`:

- Added `current_mode_size()`.
- `apply_movie_state()` now accepts `target_refresh`.
- When a target refresh is supplied, it looks for a mode matching the current
  resolution and the requested source-like refresh.

What changed in `cli/play.py`:

- Playback runs now call `choose_display_refresh_rate(mpv_args)` unless
  `--force-60hz` is used.
- The tool logs a line such as:

```text
[lg-tv-play] Refresh decision: 23.976 Hz (...)
```

- The corresponding KScreen command now includes the selected target mode
  before HDR/WCG application.

Why it mattered:

- This is the most important fix from the run.
- It changes playback from "inherit the desktop 120 Hz link" to "match the
  source refresh when possible."

## Current Code-Level Outcome

### `src/lg_tv_automation/media.py`

Current role:

- normalizes mpv args
- injects `--wayland-content-type=none` on Wayland by default
- resolves directory inputs to the most likely media file
- detects HDR from media metadata
- detects source video frame rate
- selects a playback refresh target for display-mode switching

Key outcome:

- HDR and refresh decisions are now both media-aware.

### `src/lg_tv_automation/display.py`

Current role:

- captures display mode, HDR, WCG, and VRR policy
- forces `vrrpolicy.never` for movie mode
- restores the previous VRR policy afterward
- optionally changes output mode to match source refresh

Key outcome:

- Host-side display transitions are now explicit and verifiable.

### `src/lg_tv_automation/cli/play.py`

Current role:

- handles media-based HDR decision
- handles media-based refresh decision
- supports debug snapshots and synchronized cueing
- cleans up robustly under cancellation

Key outcome:

- Playback runs are more diagnosable and restore more reliably.

### `src/lg_tv_automation/tv.py`

Current role:

- uses direct hidden writes only for main HDMI/Game Optimizer changes
- applies expanded hidden keys for VRR/ALLM/Game Optimizer
- wraps TV RPCs in timeouts
- fails fast with readable errors instead of hanging forever

Key outcome:

- The TV control path is more robust and less misleading, even though it was
  not the final root cause of the visible playback symptom.

### `src/lg_tv_automation/tv_ui.py`

Current role:

- remains a dev/status calibration tool
- threshold was adjusted for this firmware/layout

Key outcome:

- Slightly better dev-only read accuracy, but still not suitable as the final
  source of truth for the main flow.

## Tests Added / Updated

`tests/test_helpers.py` was expanded substantially.

Coverage added in this run includes:

- Wayland content-type injection behavior
- directory-to-media probe resolution
- HDR detection using resolved media path
- frame-rate parsing
- refresh-rate choice using resolved media path
- lowered UI threshold behavior
- forcing `vrrpolicy.never` in movie mode
- source-refresh mode matching
- restoring saved VRR policy in desktop mode
- JSONL debug log writing
- readable TV connect timeout on pairing problems
- readable hidden-setting timeout failures
- clean failure handling for TruMotion timeouts
- cleanup retry behavior under cancellation
- proof that main flow does not invoke UI fallback
- hidden HDMI-state ordering and payload expectations for both movie and
  desktop paths

At the time of the last validation in this run:

- `python3 -m py_compile ...` passed
- `PYTHONPATH=/home/joel/code/lg-tv-automation/src python3 -m unittest discover -s /home/joel/code/lg-tv-automation/tests`
  passed with `22/22`

## Important Evidence Directories

### User-Driven Ground Truth

- `tv-pics-test/01/`
  - synchronized debug log + user phone photos
  - proves host side looked correct while TV UI still showed movie-mode failure

### UI Calibration

- `tv-pics-test/02/ui-nav2/`
- `tv-pics-test/02/ui-capture/`

Use these only for understanding the current TV UI layout.

### Reapply-During-Playback Experiment

- `tv-pics-test/live-investigation/`

This is the evidence that simply re-sending the hidden off-writes during active
playback did not solve the visible issue.

### Refresh-Rate Differential Evidence

- `tv-pics-test/live-force60/`
- `tv-pics-test/live-force24/`

These are the most important artifacts for the final conclusion.

Recommended first image to inspect:

- `tv-pics-test/live-force24/13_down10.jpg`

That image is the strongest evidence that active playback at 24 Hz produces the
intended movie-style dashboard state.

### Final Verification Attempt

- `tv-pics-test/final-verification/`

Important warning:

- This directory should not be treated as authoritative evidence.
- The external capture flow changed the on-screen situation and was noisier than
  the single-process live experiments.

## What Was Fixed Versus What Remains Open

### Fixed In This Run

- HDR detection no longer breaks when the user passes a media directory.
- Cleanup/restore no longer falls apart under Python 3.14 cancellation.
- Main playback flow no longer uses UI fallback for HDMI/Game Optimizer changes.
- TV-side hangs are bounded by request timeouts.
- Host-side movie mode now forces `vrrpolicy.never`.
- Host-side restore now returns to the prior VRR policy.
- Debug logging exists for synchronized playback diagnosis.
- Playback now auto-matches display refresh to source FPS when feasible.

### Still Open / Not Fully Solved

- The detailed Game Optimizer / ALLM preference row may still appear enabled on
  this firmware even when the active dashboard indicators are off at 24 Hz.
- No hidden-setting payload discovered in this run reliably clears that row
  while preserving the rest of the movie-mode behavior.
- The `--wayland-content-type=none` change should be treated as a secondary
  mitigation, not the primary explanation.

## Recommended Next Steps For Future Agents

1. Do not restart from the assumption that VRR/ALLM persistence is mainly a TV
   hidden-write bug. That theory was tested hard in this run and was not the
   strongest explanation.
2. Treat the refresh-matching work as the primary fix path unless new evidence
   disproves it.
3. When validating active behavior, prefer:
   - host-side machine logs
   - synchronized TV photos
   - controlled live capture directories
4. Do not rely on screenshot-driven UI automation as the final truth source for
   active playback state.
5. If the remaining detailed ALLM row matters enough to chase further, focus on
   finding a firmware-specific hidden key for that preference row rather than
   re-litigating the already-tested playback refresh behavior.

## Practical Notes For Future Work

- The repository is still dirty after this session. Do not assume the work is
  committed.
- The current environment was KDE Wayland on Fedora with NVIDIA and a single
  HDMI-connected LG OLED. That stack behavior matters.
- `.aiopylgtv.sqlite` appeared during the session and remains untracked.
- `tv-pics-test/` is session evidence and should be preserved until someone
  intentionally decides which artifacts belong in-repo long-term.

## Bottom Line

This run fixed several real bugs and narrowed the visible movie-mode issue with
much better evidence than existed at the start.

The most important conclusion is not "LG hidden writes are still broken." The
most important conclusion is:

- the user-facing active VRR/Low Latency symptom tracked the HDMI refresh rate
  during playback much more strongly than it tracked repeated hidden off-writes
  sent to the TV

That is why the code now auto-matches the display refresh to the source video.
