---
name: lg-tv-control
description: "Maintain the workstation's LG TV movie-playback setup, inspect UI-only settings through live screenshots and individual remote keys, and diagnose playback configuration. Use for Game Optimizer, HDMI settings and TV/display troubleshooting outside the normal lg-tv-play movie command."
---

# LG TV control

The only public command is `lg-tv-play "[movie path]"`. Other setup and repair
work belongs to Codex. You supply visual reasoning; the persistent remote
transport supplies screenshots and bounded actions. No external model
API or computer-use browser is needed.

## Start and observe

Resolve the checkout from this skill's location (three parents above this
directory). Launch `.venv/bin/python -m lg_tv_automation.remote` there using a
persistent terminal session
(`exec_command` with `tty: true`, short yield). Keep that session id. The first
JSON responses announce readiness and an absolute screenshot path. Open that
file with `view_image`, preferably original detail.

Send one JSON object and newline with `write_stdin`. Each action returns a new
image path, dimensions, active app and sequence number. Open the returned image
before choosing the next action. The transport does not infer focus or settings.

```json
{"op":"observe"}
{"op":"button","key":"ADVANCE_SETTING"}
{"op":"button","key":"DOWN"}
{"op":"button","key":"ENTER"}
{"op":"launch","app":"com.webos.app.gameoptimizer"}
{"op":"quit"}
```

These are separate requests, not a scripted route. Supported keys: UP, DOWN,
LEFT, RIGHT, ENTER, BACK, MENU, QMENU, ADVANCE_SETTING. HOME and EXIT are refused
because they can send this workstation's user to the TV home menu.
Launch requests are limited to Game Optimizer and HDMI inputs; arbitrary apps,
including TV home, are refused.
Optional `settle` is 0–3 seconds, default 0.35 after a button and 0.8 after launch.
If an image is mid-transition, request another observation with `settle: 0.5`.

## Navigate and verify

- Choose actions from visible labels and focus in the latest image. Do not use
  remembered menu offsets, color thresholds, fixed counts or blind key batches.
- If focus is unclear, use a single directional key and compare fresh images.
  If stuck, BACK and re-observe; do not repeatedly toggle a control. If needed,
  launch HDMI 1 directly rather than opening the TV home menu.
- Before changing a setting, record its visible original value. Change only
  settings needed for the user's request, then verify their displayed value.
  Read-only inspection must not temporarily enable Game Optimizer to reveal
  disabled controls: report them as unavailable instead.
- If `ok` is false, inspect `action_applied`: true means the action completed
  but observation failed; "unknown" means it may have completed. Observe again
  before considering another action. API acknowledgment alone is not proof of
  a visible setting change. A blank/DRM screenshot is not evidence of state.
- The workstation is visible through HDMI 1. Do not change network, pairing,
  firmware, reset settings or HDMI source unless specifically requested.
- If a diagnostic requires temporary changes, restore each original value and
  visually verify it before ending. Session cleanup returns to HDMI 1, but does
  **not** restore settings you changed or infer whether an overlay is open.

Before quitting after navigation, close visible settings overlays with single
BACK actions and verify the bare HDMI screen. The transport cannot infer whether
an overlay is still open, and launching HDMI alone may leave settings visible.
Always send `quit` and wait for `event: closed`. Observation-only sessions send
no cleanup keys and leave the current screen/input untouched. Once navigation
has been attempted, cleanup checks the input and launches HDMI 1 only if needed;
it never sends a blanket EXIT key, which can open the TV home menu. On EOF,
SIGINT, SIGTERM or 120
seconds idle, the session attempts the same bounded cleanup. A hard kill cannot
guarantee cleanup. The session owns the same exclusive lock as `lg-tv-play`:
quit before running playback, and do not bypass the lock during playback.
Interrupted sessions may leave a settings overlay open; reconnect and close it
visually if needed. Screenshots remain in a private per-session state directory;
don't publish them.

## Playback boundary

Keep the normal `lg-tv-play` path menu-free for startup latency. Reliable direct
API/display/media operations remain ordinary code. Use this skill for UI-only
preparation or troubleshooting, close the session, then invoke playback if the
user requested it. The shell wrapper does not itself run a vision model.
For transport responses and failure/cleanup semantics, read
[references/transport.md](references/transport.md) when needed.
For the accepted EDID/input-classification baseline, no-signal failure modes or
host setup/recovery, read [references/workstation.md](references/workstation.md).
Do not change boot configuration or firmware without the user's explicit request.
