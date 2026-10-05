#!/usr/bin/env bash
set -euo pipefail

# The sole public command. Codex invokes its maintenance transport privately.
SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
TARGET="$REPO_ROOT/bin/lg-tv-play"
DEST_DIR="${XDG_BIN_HOME:-$HOME/.local/bin}"
LINK="$DEST_DIR/lg-tv-play"

if (($#)); then
    printf 'Usage: scripts/install-command-links.sh\n' >&2
    exit 2
fi
if [[ ! -x "$TARGET" ]]; then
    printf 'Missing playback wrapper: %s\n' "$TARGET" >&2
    exit 1
fi
if [[ -e "$LINK" && ! -L "$LINK" ]]; then
    printf 'Refusing to overwrite non-symlink: %s\n' "$LINK" >&2
    exit 1
fi
mkdir -p "$DEST_DIR"
ln -sfn "$TARGET" "$LINK"
printf '%s -> %s\n' "$LINK" "$TARGET"
if [[ ":$PATH:" != *":$DEST_DIR:"* ]]; then
    printf 'Warning: %s is not on PATH.\n' "$DEST_DIR" >&2
fi
