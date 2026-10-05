# Visual-agent TV control

The project separates reliable mechanics from UI reasoning. `lg-tv-play` keeps
its direct API/display/media work. The private `lg_tv_automation.remote`
transport keeps one connection open and
performs one requested action; Codex uses the `lg-tv-control` repository skill
to interpret screenshots and choose the next step.

## Protocol

Start `.venv/bin/python -m lg_tv_automation.remote` from the checkout in a
persistent terminal (`tty: true`). Read the initial
`ready` event and observation. Send one JSON object per line:

| Request | Effect |
| --- | --- |
| `{"op":"observe"}` | Screenshot and current app; no remote action |
| `{"op":"button","key":"DOWN"}` | One key, then screenshot |
| `{"op":"launch","app":"com.webos.app.gameoptimizer"}` | Launch app, then screenshot |
| `{"op":"quit"}` | Close overlays, verify return input, disconnect, release lock |

Allowed keys are navigation/settings keys, not power, volume, factory reset or
arbitrary API writes. Optional `settle` is 0–3 seconds. Default wait is 0.35s
after keys, 0.8s after launch, zero for observation. These are small capture
delays, not claims that a page has finished loading. Inspect the image and
observe again when it is still transitioning.

Successful observations include `screenshot` (absolute private PNG path), native
`width`/`height`, `current_app` and `sequence`. Settings overlays can leave
`current_app` as HDMI 1; use the image to identify the current page/focus.
The screenshot endpoint on this TV currently supplies 960×540; the transport
preserves whatever native size it receives, unlike the old resized detectors.

Failures include `action_applied`: false for no action, true for a successful
action followed by an observation failure, or `"unknown"` for a failed action
round trip. Observe before retrying. Mutating actions are never auto-retried.
Malformed/unknown requests are rejected before sending anything to the TV.
Pairing, connection, individual requests, downloads and cleanup have deadlines.
The 120s idle timeout is configurable with `--idle-timeout` (10–600s).

Screenshot URLs must be hosted by the configured TV; redirects and oversized
downloads are refused. The TV's self-signed HTTPS certificate is accepted only
for that screenshot download. Pairing uses the same private configuration as
playback. No external model service or credentials are added.

## Cleanup limits

The remote session holds the exclusive playback lock. No `lg-tv-play` run can
change the display/profile beneath it. Close the remote before playback.
EOF, quit, idle expiration and normal signals attempt EXIT, verify the return
input, and disconnect, including on failures. Cleanup is bounded to 20s and
failures are reported. SIGKILL, network loss and power failure cannot guarantee
return. The transport does not restore setting values: the agent must record
and restore any temporary changes, verifying the visible result.

## Real TV validation, 2026-10-05

An actual session used 21 fresh screenshots, no scripted UI route or detector,
to open Settings, select General by visible focus, open Game Optimiser, return
to General, scroll to External Devices, and open HDMI Settings. Screenshots
showing loading spinners were followed by new observations. The General menu
changed layout after returning from Game Optimiser; navigation continued from
the new image rather than a remembered row count.

The observed desktop Game Optimiser dashboard showed VRR OFF and Low Latency
ON. HDMI Settings showed 4:4:4 Pass Through ON, Quick Media Switching OFF,
SIMPLINK ON and Dolby Vision PC OFF. No switches or profile values were changed.
`quit` completed successfully and verified HDMI 1 as the active app.
Screenshots are intentionally not checked in because they include the desktop.

This validates interactive navigation, not autonomous model execution inside
the shell playback command or a promise that reasoning through menus is faster
than direct APIs. Hardware-free regressions cover request validation, native
image preservation, action/capture failures, EOF/idle/cancellation cleanup,
disconnect after EXIT failure, and contention with the playback lock.
