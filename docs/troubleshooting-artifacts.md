# Troubleshooting Artifact Map

This repo contains the stable automation workflow. Bulky generated evidence
stays in:

```text
/home/joel/Documents/system-troubleshooting/2026-04-16-mpv-dropped-frames
```

## HDR 119 Hz EDID Evidence

Primary external entry point:

```text
/home/joel/Documents/system-troubleshooting/2026-04-16-mpv-dropped-frames/evidence/P4/README.md
```

Key artifacts:

- `evidence/P4/edid/`
  - Original generated scripts and EDID blobs from the investigation.
  - The stable versions are now packaged in this repo.
- `evidence/P4/edid-override-validation-2/`
  - Settled LG Game Optimizer dashboard proof:
    `120 FPS`, `VRR OFF`, `Low Latency OFF`.
- `evidence/P4/edid-override-stress-30min/`
  - IPC-sampled playback stress run.
  - The primary summary is `summary-from-samples.json`.
- `evidence/P4/edid-override-user-success-2026-04-20.md`
  - User full-episode success report after the override.

## Reports

Primary report for the accepted mitigation:

```text
/home/joel/Documents/system-troubleshooting/2026-04-16-mpv-dropped-frames/reports/report-005-hdr119-allm-vrr-edid-override.md
```

Execution timeline:

```text
/home/joel/Documents/system-troubleshooting/2026-04-16-mpv-dropped-frames/plans/execution-log-2026-04-16-mpv-dropped-frames.md
```

## Legacy UI Evidence

April 9 LG Game Optimizer UI screenshots from the earlier automation
investigation are preserved outside this checkout:

```text
/home/joel/Documents/system-troubleshooting/2026-04-09-lg-tv-automation-ui-evidence/tv-pics-test
```

The historical handoff at
`docs/handoff-hdr-vrr-allm-investigation-2026-04-09.md` still refers to those
files with their original `tv-pics-test/...` relative paths.

## What Belongs Here Versus There

Keep in `lg-tv-automation`:

- stable EDID assets
- install/remove/verify tooling
- command documentation
- code changes needed for VRR-incapable EDID handling

Keep in `system-troubleshooting`:

- screenshots
- long `mpv` IPC samples
- one-off probe logs
- historical reports and execution logs
