#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage: scripts/install-command-links.sh [--user|--system] [--force]

Install command symlinks that point at this checkout's bin/ wrappers.

Modes:
  --user    Link into ~/.local/bin. This is the default.
  --system  Link into /usr/local/bin. Run this mode with sudo.

Options:
  --force   Move conflicting regular files aside before linking.
EOF
}

mode="user"
force="no"

while (($#)); do
    case "$1" in
        --user)
            mode="user"
            ;;
        --system)
            mode="system"
            ;;
        --force)
            force="yes"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            printf "Unknown argument: %s\n\n" "$1" >&2
            usage >&2
            exit 2
            ;;
    esac
    shift
done

SCRIPT_PATH="$(readlink -f "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
commands=(lg-tv-play lg-tv-ui-probe lg-tv-edid)

if [[ "$mode" == "system" ]]; then
    dest_dir="/usr/local/bin"
    if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
        printf "System mode must run as root: sudo %q --system\n" "$SCRIPT_PATH" >&2
        exit 1
    fi
else
    dest_dir="${XDG_BIN_HOME:-$HOME/.local/bin}"
fi

mkdir -p "$dest_dir"

for command in "${commands[@]}"; do
    target="$REPO_ROOT/bin/$command"
    link="$dest_dir/$command"

    if [[ ! -x "$target" ]]; then
        printf "Missing executable wrapper: %s\n" "$target" >&2
        exit 1
    fi

    if [[ -L "$link" ]]; then
        ln -sfn "$target" "$link"
    elif [[ -e "$link" ]]; then
        if [[ "$force" != "yes" ]]; then
            printf "Refusing to overwrite non-symlink: %s\n" "$link" >&2
            printf "Re-run with --force to move it aside.\n" >&2
            exit 1
        fi
        backup="$link.bak.$(date +%Y%m%d%H%M%S)"
        mv "$link" "$backup"
        printf "Moved existing file aside: %s\n" "$backup"
        ln -s "$target" "$link"
    else
        ln -s "$target" "$link"
    fi

    printf "%s -> %s\n" "$link" "$target"
done

if [[ "$mode" == "user" && ":$PATH:" != *":$dest_dir:"* ]]; then
    printf "Warning: %s is not currently on PATH.\n" "$dest_dir" >&2
fi
