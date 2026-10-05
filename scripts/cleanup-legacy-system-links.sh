#!/usr/bin/env bash
set -euo pipefail

# Remove only the two obsolete symlinks belonging to this checkout. This never
# touches their former targets, the playback command, firmware or boot settings.
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
DRY_RUN=false
if [[ "${1:-}" == "--dry-run" ]]; then
    DRY_RUN=true
    shift
fi
if (($#)); then
    printf 'Usage: sudo scripts/cleanup-legacy-system-links.sh [--dry-run]\n' >&2
    exit 2
fi
if [[ "$DRY_RUN" == false && "$EUID" -ne 0 ]]; then
    printf 'Run with sudo, or use --dry-run to inspect without changes.\n' >&2
    exit 1
fi

NAMES=(lg-tv-edid lg-tv-ui-probe)
# Preflight every target before removing either, refusing files and foreign links.
for NAME in "${NAMES[@]}"; do
    LINK="/usr/local/bin/$NAME"
    if [[ -L "$LINK" ]]; then
        if [[ "$(readlink -- "$LINK")" != "$REPO_ROOT/bin/$NAME" ]]; then
            printf 'Refusing unrelated symlink: %s\n' "$LINK" >&2
            exit 1
        fi
    elif [[ -e "$LINK" ]]; then
        printf 'Refusing non-symlink: %s\n' "$LINK" >&2
        exit 1
    fi
done

for NAME in "${NAMES[@]}"; do
    LINK="/usr/local/bin/$NAME"
    if [[ ! -L "$LINK" ]]; then
        printf 'Already absent: %s\n' "$LINK"
    elif [[ "$DRY_RUN" == true ]]; then
        printf 'Would remove symlink: %s -> %s\n' "$LINK" "$REPO_ROOT/bin/$NAME"
    else
        unlink -- "$LINK"
        printf 'Removed symlink: %s (target files untouched)\n' "$LINK"
    fi
done
