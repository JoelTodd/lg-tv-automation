# Handoff: KDE Discover Stuck After Display Automation

## Summary

During investigation of a user-visible symptom in KDE Plasma, clicking `Discover`
from the task manager showed the busy cursor but did not surface a usable
window. The problem was not a broken `.desktop` launcher and did not primarily
point to Discover's package backends.

The stronger signal was that the local Plasma Wayland session had entered a bad
display state after output transitions. This repo is directly relevant because
it intentionally drives Fedora-side display changes through `kscreen-doctor`,
including HDR, WCG, and optional mode switching on a single HDMI-connected LG
OLED.

That behavior is in scope for the project. The failure mode below is therefore
worth documenting as an operating constraint, not as an unrelated desktop quirk.

## What Was Observed

- `org.kde.discover.desktop` was valid and launched `plasma-discover` normally.
- Discover had an existing long-running single-instance process in the session.
- Clicking Discover again from the task manager only targeted that existing
  process.
- Quitting the existing Discover D-Bus app and relaunching it made it open
  again.

More importantly, the surrounding Plasma session logs showed repeated global
display instability:

- `There are no outputs - creating placeholder screen`
- `requesting unexisting screen available rect -1`

Those messages were not limited to Discover. They appeared across multiple KDE
processes, which means the session temporarily lost its usable output topology.

## Why This Repo Matters

The repo already documents the core risk:

- `README.md` states that Fedora-side HDR/WCG toggling is handled through
  `kscreen-doctor`.
- `README.md` also notes that the display stack can briefly drop the output
  during HDR transitions.
- `src/lg_tv_automation/display.py` implements retry logic around
  `kscreen-doctor -j` specifically because the output can disappear transiently.

That means the observed Plasma symptom is consistent with the current design:

1. `lg-tv-automation` changes KScreen output state.
2. On KDE Wayland with NVIDIA and a single HDMI output, the output may briefly
   disappear or remap badly.
3. Long-running GUI apps can survive in a stale state after the output returns.
4. Single-instance apps such as Discover may then appear "stuck" when launched
   again, because the session routes activation to the stale process instead of
   opening a fresh visible window.

This does not mean the project is doing the wrong thing. It means the project's
known operating environment has a fragile edge, and the documentation should
treat that fragility as part of the contract.

## Relevant Environment Signals

The local machine matched the repo's expected environment closely:

- Fedora KDE on Wayland
- NVIDIA proprietary stack
- single HDMI output
- KScreen output transitions driven by `kscreen-doctor`

Additional session noise also pointed to display-session fragility:

- repeated `plasma-keyboard` crashes or EGL swap failures
- repeated placeholder-screen events across the session
- output/audio object churn around suspend/resume and HDMI state changes

Those signals reinforce the same conclusion: the issue is best understood as a
display-stack side effect, not as a Discover-specific logic bug.

## Suggested Fixes That Fit The Project DNA

The project should stay small, direct, and centered on reliable TV/display
automation. The repo should not grow into a general KDE session repair tool.
With that constraint in mind, the useful fixes are the ones that improve
operator awareness and reduce bad-state frequency without bloating scope.

### 1. Document the Side Effect Explicitly

Recommended as the highest-value change.

Add a note near the Fedora display control section explaining that:

- KScreen transitions can briefly remove the output from Plasma.
- Some already-running GUI apps may need to be restarted afterward.
- This is especially relevant on KDE Wayland with NVIDIA and a single HDMI
  output.

Why this fits:

- It matches the existing README tone.
- It treats the behavior as an operational constraint.
- It avoids pretending the repo can fully paper over compositor-level behavior.

### 2. Encourage `--no-display` When Only TV-Side Changes Are Needed

Recommended as a documentation-first mitigation.

The CLI already exposes `--no-display`. It should be positioned more strongly as
the preferred mode when the user only wants TV-side picture/input changes and
does not need HDR/WCG/mode changes on Fedora.

Why this fits:

- No new surface area is required.
- It preserves the current command model.
- It reduces unnecessary churn in the most fragile subsystem.

### 3. Keep Recovery Guidance Small And Manual

Recommended as a short troubleshooting note, not as new automation.

If a display transition leaves a GUI app stranded, restarting the app is a
reasonable operator action. For Discover specifically, this worked:

```bash
busctl --user call org.kde.discover /MainApplication org.qtproject.Qt.QCoreApplication quit
gtk-launch org.kde.discover.desktop
```

Why this fits:

- It is useful during real use of the workstation.
- It does not force desktop-session assumptions into the core tool.
- It keeps the project focused on the TV/display workflow.

## Fixes That Do Not Fit The Current Scope

These are intentionally not recommended unless the repo's scope expands:

- building generic KDE/Plasma app restart orchestration into `lg-tv-automation`
- adding compositor repair logic
- trying to manage or reset Discover, plasmashell, or unrelated desktop apps
- turning the repo into a suspend/resume or Wayland session health manager

Those ideas conflict with the current design DNA: small command surface, focused
ownership, and direct control over the TV/display path only.

## Documentation Status

This note has been folded into `README.md`. The current README states this
failure mode plainly:

- display changes can temporarily drop the output
- on this stack, some desktop apps may be left in a stale state afterward
- `--no-display` is the preferred escape hatch when Fedora-side display changes
  are unnecessary

Keeping this handoff is still useful because it records the observed KDE
symptom and the manual recovery command, but the README is the current
operator-facing source of truth.
