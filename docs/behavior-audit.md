# Playback and maintenance behavior audit — 2026-10-09

Reviewed every project module, command wrapper, setup script and existing test,
including the installed TV library's connection/storage behavior. This follows
the initial [post-update validation](workstation-validation.md).

## Confirmed findings and changes

| Finding | Reproduction/evidence | Result |
| --- | --- | --- |
| Invalid media still opened a TV session and could prompt for pairing. | The probe, connection and display capture previously ran together. | Probe the media first; connect and capture the display only after success. These latter reads still overlap. |
| A display-preparation failure could force HDMI 1 despite never starting TV preparation. | Regression tests reproduced `return_to_input=True` after a probe/display failure. | Only restore the TV input when TV preparation was attempted. |
| Remote cleanup relaunched already-active HDMI. | The first cleanup fix from this session explicitly launched HDMI after every navigation action; launching it did not dismiss Settings. | Check the current app and launch HDMI only if needed. Close visible menus with individual BACK actions before quitting. |
| HOME/EXIT remained available despite the user's request to avoid the TV home menu. | EXIT caused the earlier reported interruption; neither key is needed for the verified Settings/BACK workflow. | Refuse both keys in the private transport. |
| Each profile repeated four hidden-setting alerts and their one-second settling wait even when picture/motion settings needed no writes. | Test reproduced two batches with both settings already satisfied. Live desktop retest now used one batch in 1.32 s with no failures. | Repeat the batch only after an attempted picture/motion write. Keep the second batch when a failed response leaves the result uncertain. |
| A hidden-setting alert with no `alertId` was accepted as success. | Previously skipped the close callback, which is what applies the setting. | Raise a clear failure instead of claiming an acknowledged write. |
| Probe accepted attached cover artwork as a video. | Real audio-with-cover fixture: `v:0` returned a PNG stream; `V:0` returned no playable video. | Exclude attached pictures, thumbnails and cover art. |
| Explicit SDR transfer metadata lost to the BT.2020/10-bit HDR heuristic. | Test reproduced a false HDR result for `bt709` transfer with BT.2020 primaries and 10-bit pixels. | Explicit transfer metadata wins; retain the fallback only when transfer metadata is unavailable. |
| Pairing migration searched the invoking directory. | A first-use test with an unrelated caller database demonstrated the unsafe source choice. | Migrate from this checkout or the user's home, independent of the calling directory. Preserve existing pairing contents; fix permissions only when needed. |
| Cancelling during remote disconnect could be reported as success. | Cleanup's cancellation result was discarded. | Complete bounded disconnect, then preserve cancellation. |
| Plain library retry messages could appear among remote JSON responses. | A real earlier session printed `Connection attempt: 2`; installed library source confirms stdout prints on connection/button retries. | Route library connection/request diagnostics to stderr; test JSON stdout remains clean. |
| Benchmark could exit successfully without observing readiness or counters. | It returned only the wrapper's exit code and omitted unavailable properties. | Fail incomplete measurements. Record actual decoder, dimensions and colour parameters. Print temporary mpv diagnostics on failure. |
| Benchmark inherited paused/idle/keep-open/loop settings and had no run deadline. | A timed sample could stay open indefinitely; termination also waited without a bound. | Override those options for measurement only. Bound the run and graceful cleanup, and isolate the owned wrapper/player process group for last-resort termination. |
| Interrupting the benchmark could leave a child during process creation, or print a traceback for an ordinary Ctrl-C. | Real Ctrl-C completed restoration but produced a `KeyboardInterrupt` traceback and exit 1. A child-process regression exercises cancellation between fork and handle return. | Shield creation and cleanup, reap a late child, and exit 130 quietly after normal interruption. |
| Malformed probe metadata could escape validation. | Tests exercise invalid top-level/stream shapes. | Convert malformed metadata into a readable probe failure. |
| Display capture accepted a disabled/disconnected output or no active mode. | It stored an unusable snapshot and allowed movie preparation to continue. | Reject those states before any display/profile writes; test all three cases. |

The cover-art selector follows [FFprobe's documented stream specifiers](https://ffmpeg.org/ffprobe.html#Stream-specifiers).
Decoder/colour telemetry uses [mpv's documented properties](https://mpv.io/manual/stable/#properties).

## Actual workstation checks

Used generated local test patterns, not downloaded media. The HDR fixture
exposes PQ, BT.2020, 10-bit HEVC metadata before playback; both are 3840×2160 at
24 fps. The workstation display remains at 119.88 Hz.

| Run | Readiness | Duration | Decoder | Colour reported by mpv | Decoder/output drops | Exit |
| --- | ---: | ---: | --- | --- | ---: | ---: |
| SDR H.264 | 4.88 s | 4 s | Vulkan hardware decoding | BT.1886 / BT.709 | 0 / 0 | 0 |
| HDR HEVC Main 10 | 5.64 s | 30 s | Vulkan hardware decoding | PQ / BT.2020 | 0 / 0 | 0 |

- Public `lg-tv-play` from a different directory completed a six-second SDR
  clip and restoration, without creating a caller-directory pairing database.
- Audio-with-cover was rejected in 0.14 s with a deliberately unreachable TV
  override. No connection attempt, pairing prompt or display preparation occurred.
- A real Ctrl-C during 4K HDR playback stopped/reaped mpv, exited 130, and
  restored connected/enabled HDMI-A-1 mode 14 with HDR/WCG off. No mpv process
  remained afterward.
- Reapplying the already-satisfied desktop picture/label used one hidden HDMI
  batch, verified `expert1` and live signal, and reported no failures. Hidden
  switch values remain visibly unverified through the API.
- Live Settings/BACK/quit inspection returned to the bare HDMI desktop. The
  transport now verifies already-active HDMI without relaunching it.
- Real Ctrl-C during benchmark preparation completed restoration and exited 130
  without a traceback. The benchmark interruption after playback began also
  stopped its isolated wrapper/player group and left no mpv process running.
- Final independent reads confirmed HDMI 1, live signal, desktop label,
  `expert1`, and display mode 14 with HDR/WCG off. Removed the temporary audit
  clips, this audit's screenshots and generated caches.

Initial automated validation: 96 tests and 42 subtests passed, including real
subprocess cancellation/reaping tests. Dependency consistency, shell syntax,
read-only legacy-link preflight and Git whitespace checks also pass.

## Further removal and remediation

Removed unused verification-summary dictionaries, duplicate connection state,
an unused subprocess error-checking option, dormant debug logging and its
descriptions, unread HDR explanation strings, and PID writes to the lock file.
The operating-system lock still prevents concurrent sessions; it doesn't need
file contents to work. Actual setting reads, signal checks, error reporting,
fallback paths, ordered writes and cancellation/restoration remain.

Both real profiles switch all four desktop HDMI features together. Replaced
their four independent booleans and optional state object with one explicit
desktop-feature flag, and merged duplicate VRR/ALLM write branches. Recorded
the old TV requests before editing and compared 192 combinations of profiles,
already-satisfied settings, active input and picture-mode fallback failures.
Every request, payload, ordering and reported failure remained identical.

Media probe errors now raise ordinary exceptions directly, replacing error
dictionaries and text-prefix matching. The Wayland content hint is an explicit
player option, eliminating environment-dependent argument rewriting. KScreen
retries malformed JSON structures as well as invalid JSON text.

Pairing migration used SQLite transaction contexts as if they closed database
handles. They do not. Migration now closes both connections; a regression
checks they are unusable afterward and the keys remain intact. The tests no
longer emit database resource warnings. The private remote now allows launches
only for Game Optimizer and HDMI inputs, closing the alternative route to TV
home through an app launch.

### Player shutdown fault exposed by live retesting

Two four-second SDR H.264 runs reached EOF with zero decoder/output drops but
stalled during shutdown. A live stack trace showed the main thread joining
video output, while video output waited in `vk_gpu_finish` / `vk_cmd_poll` and
NVIDIA's `libnvidia-glcore.so.615.71.09`. Automatic hardware selection chose
Vulkan Video. SIGTERM to the wrapper stopped/reaped the stuck player and
completed desktop restoration on both runs.

A playback-only `--hwdec=nvdec` option avoids the reproduced stall while keeping
Vulkan rendering. [mpv documents NVDEC support with Vulkan and recommends it for
NVIDIA hardware](https://mpv.io/manual/stable/#options-hwdec). The experiment
isolates the decoder choice as a successful workaround; it does not establish
which upstream component caused the fault. Global player settings are preserved.

| Retest | Readiness | Playback sample | Decoder | Colour | Drops | Exit |
| --- | ---: | ---: | --- | --- | ---: | ---: |
| SDR H.264, decoder override trial | 4.46 s | 4 s | NVDEC | BT.1886 / BT.709 | 0 / 0 | 0 |
| HDR HEVC Main 10, final wrapper | 5.80 s | 8 s | NVDEC | PQ / BT.2020 | 0 / 0 | 0 |

The HDR retest fixture's PQ/BT.2020 metadata was verified before playback.
An earlier newly generated HEVC fixture lacked its requested transfer metadata;
it was correctly treated as SDR and excluded from HDR validation.

The final public command also played the complete twelve-second SDR fixture,
exited 0 at EOF and restored the desktop. A competing command was refused while
HDR playback owned the lock. Cancelling confirmed HDR playback at one second
stopped/reaped mpv, restored the desktop and returned 130. Invalid media with
an unreachable TV override failed at the probe without hardware preparation.

Automated retest: 93 tests and 50 subtests pass. Three removed tests exercised
the deleted argument-rewriting helper; the retained playback test checks the
actual player options and movie delimiter. This removal pass reduces runtime
code by 95 lines while retaining the critical TV request sequences.
Final independent checks found HDMI 1 active with live signal, desktop label
and `expert1`, and connected/enabled display mode 14 with HDR/WCG off. A fresh
TV screenshot showed the workstation with no settings overlay; observation-only
quit sent no navigation action. Removed this pass's temporary fixtures, IPC,
diagnostic logs, screenshot and generated caches. No player remained running.

## Checks retained deliberately

The remaining hidden HDMI batch and settling wait are necessary to this
workstation's accepted movie-processing setup. The API cannot reliably read
their visible state, so removing them based only on an acknowledgment would be
guesswork. Their existing order and settings were preserved.

Picture-mode fallback APIs run only when an earlier path fails verification.
Signal checks guard against the documented case where KScreen reports an
enabled output but the TV has no signal. The shared session lock and bounded
child-stop/restoration logic prevent competing changes and cleanup races.
Normal desktop cleanup remains the established SDR `expert1` profile.

No boot/firmware, EDID, global mpv configuration or unrelated service changes
were needed. Preserved pairing credentials and the tracked recovery assets.

## Limits

### Live TV-menu verification on 9 October 2026

Read-only inspection used fresh TV screenshots after each individual remote
key. The diagnostic applied the normal display and TV profile functions with
the shared lock; no movie was playing during menu inspection.

| Visible setting | Desktop baseline | SDR movie | HDR movie |
| --- | --- | --- | --- |
| Picture mode | Expert (Bright space, daytime) | FILMMAKER MODE | HDR FILMMAKER MODE |
| Game Optimiser master | On | Off | Off |
| Low Latency dashboard | On | Off | Off |
| VRR & G-Sync setting | On | Off | Off |
| 4:4:4 Pass Through | On | Off | Not yet inspected |
| TruMotion | Not inspected | Cinematic Movement selected | Not yet inspected |

Desktop HDMI Deep Colour was visibly set to 4K. Quick Media Switching and
Dolby Vision PC were off, and SIMPLINK was on, in both the desktop and SDR
inspections. These preferences were not changed. Live VRR was off even with
the desktop VRR toggle on, consistent with the accepted EDID removing VRR.
Internal ALLM and related flags were not independently visible; the Low
Latency dashboard supplies evidence of the effective latency state.

The TV lost its HDMI signal during read-only HDR Clarity navigation. KScreen
still reported a connected output; no settings write occurred at that moment.
The user unplugged and replugged HDMI and the picture returned. A temporary
60 Hz diagnostic overlapped the reconnect, so recovery cannot be attributed
to that diagnostic. The underlying cause remains unresolved. After recovery,
HDR Game Optimiser, Low Latency and VRR settings were visually checked again.

At the user's request to stop for a movie, further inspection ended. The normal
desktop profile was restored without reported failures: `expert1`, HDMI 1
classification, desktop HDMI feature writes on, original mode 14 (119.88 Hz),
HDR/WCG off, and live HDMI signal present. A fresh screenshot confirmed the
bare workstation with no TV overlays. The observation-only remote was then
closed. Final individual desktop toggles were not reopened for inspection.
Screenshots remain private maintenance artifacts.

### Completed checks and restoration repair on 10 October 2026

The remaining HDR checks confirmed selected, available Cinematic Movement and
4:4:4 Pass Through off. Quick Media Switching and Dolby Vision PC stayed off;
SIMPLINK stayed on. Inspection used the app's actual profile functions and
individual remote keys, without playing a movie or toggling the inspected
controls. The HDMI picture remained stable through today's checks.

The first desktop restoration returned no failures, but a subsequent API read
and fresh Picture-menu screenshot both showed `filmMaker`, not `expert1`.
Correcting the mode in settled SDR held. Two diagnostic transitions subsequently
restored correctly, so the exact step causing the intermittent drift is still
unproven. This is separate from the earlier No Signal incident.

Profile application now checks the picture mode again after completing the
HDMI changes. If necessary it repairs the mode once, restores Cinematic
Movement in that corrected movie mode, reapplies the HDMI flags, and verifies
the resulting mode and movie motion setting. A persistent mismatch is a final
profile failure: playback stops and cleanup reports failure. An already-correct
mode adds one read, with no extra setting writes or settling delay.

The updated HDR-to-desktop transition returned no failures. After settling,
both the API and live menu showed `expert1`. The completed visual checks are:

| Setting | SDR movie (9 October) | HDR movie (9–10 October) | Restored desktop (10 October) |
| --- | --- | --- | --- |
| Picture mode | FILMMAKER MODE | HDR FILMMAKER MODE | Expert (Bright space, daytime) |
| TruMotion | Cinematic Movement selected | Cinematic Movement selected | Off |
| Game Optimiser master | Off | Off | On |
| Low Latency dashboard | Off | Off | On |
| VRR & G-Sync setting | Off | Off | On |
| 4:4:4 Pass Through | Off | Off | On |

Final desktop Deep Colour was still 4K. Quick Media Switching and Dolby Vision
PC were off, and SIMPLINK was on. Live VRR stayed off as expected with the
accepted EDID. Internal ALLM/Quick Game/input-optimization flags still lack
separate visible controls; Low Latency confirms the effective latency state.

Final display state: original mode 14 (3840×2160 at 119.88 Hz), connected and
enabled, HDR/WCG off. HDMI 1 had its desktop label/icon and live signal. All
menus were closed with individual BACK keys and the bare workstation was
visually confirmed before closing the remote. No refresh-rate cycling, EDID,
boot, firmware, global player or unrelated service changes were made today.

All 95 automated tests pass. New stateful regressions cover successful repair,
drift persisting after the single repair, and restoration of movie motion when
repairing its picture mode. The live checks establish visible profile behavior,
not the cause of the intermittent mode drift or long-term HDMI stability.

These synthetic checks cover startup, a 30-second 4K HDR sample, cancellation
and restoration. They do not establish feature-length stability, audio sync,
Dolby Vision behavior, or visible confirmation of every hidden TV switch.
Files with multiple playable video streams still use the first stream for
probing; custom mpv track-selection rules can choose a different stream.
Screenshots remain private maintenance artifacts; they are not taken during
normal playback or committed to the repository.
