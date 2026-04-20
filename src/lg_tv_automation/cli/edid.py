"""Manage the local HDR 119 Hz LG EDID override."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any

from ..process import run_command


DEFAULT_CONNECTOR = "HDMI-A-1"
DEFAULT_FIRMWARE_ROOT = Path("/usr/lib/firmware")
DEFAULT_PROC_CMDLINE = Path("/proc/cmdline")
DEFAULT_SYSFS_DRM = Path("/sys/class/drm")

PATCHED_EDID = "lg-tv-sscr2-no-allm-vrr.bin"
ORIGINAL_EDID = "lg-tv-sscr2-original.bin"
PATCHED_SHA256 = "556231a8f459cecc687a28e165063698017cf8aca6cd7ed0101a7f330ee676d3"
ORIGINAL_SHA256 = "c5bd1e2b1edc8d7dabc70d87bf481fc9ba680056101241e97d0307b376077426"


def firmware_relative_path() -> Path:
    """Return the kernel firmware-relative path for the patched EDID."""

    return Path("edid") / PATCHED_EDID


def kernel_arg(connector: str = DEFAULT_CONNECTOR) -> str:
    """Return the kernel command-line argument for this override."""

    return f"drm.edid_firmware={connector}:{firmware_relative_path().as_posix()}"


def package_edid_bytes(filename: str = PATCHED_EDID) -> bytes:
    """Read a packaged EDID asset."""

    return files("lg_tv_automation.assets.edid").joinpath(filename).read_bytes()


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 digest for bytes as lowercase hex."""

    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest for a file."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def firmware_dest(firmware_root: Path = DEFAULT_FIRMWARE_ROOT) -> Path:
    """Return the absolute firmware destination path."""

    return firmware_root / firmware_relative_path()


def read_cmdline(path: Path = DEFAULT_PROC_CMDLINE) -> list[str]:
    """Read the kernel command line as tokens."""

    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").split()


def live_edid_path(connector: str = DEFAULT_CONNECTOR, sysfs_drm: Path = DEFAULT_SYSFS_DRM) -> Path | None:
    """Find the live DRM EDID path for a connector name such as HDMI-A-1."""

    matches = sorted(sysfs_drm.glob(f"card*-{connector}/edid"))
    return matches[0] if matches else None


def kscreen_output(connector: str = DEFAULT_CONNECTOR) -> dict[str, Any] | None:
    """Return KScreen's output entry when kscreen-doctor is available."""

    try:
        config = json.loads(run_command(["kscreen-doctor", "-j"]).stdout)
    except Exception:
        return None

    for output in config.get("outputs", []):
        if output.get("name") == connector:
            return output
    return None


def collect_status(
    *,
    connector: str = DEFAULT_CONNECTOR,
    firmware_root: Path = DEFAULT_FIRMWARE_ROOT,
    proc_cmdline: Path = DEFAULT_PROC_CMDLINE,
    sysfs_drm: Path = DEFAULT_SYSFS_DRM,
    include_kscreen: bool = True,
) -> dict[str, Any]:
    """Collect install and runtime status for the EDID override."""

    package_data = package_edid_bytes()
    dest = firmware_dest(firmware_root)
    live = live_edid_path(connector, sysfs_drm)
    wanted_arg = kernel_arg(connector)
    cmdline = read_cmdline(proc_cmdline)

    payload: dict[str, Any] = {
        "connector": connector,
        "kernel_arg": wanted_arg,
        "package": {
            "filename": PATCHED_EDID,
            "sha256": sha256_bytes(package_data),
            "expected_sha256": PATCHED_SHA256,
        },
        "installed": {
            "path": str(dest),
            "exists": dest.exists(),
            "sha256": sha256_file(dest) if dest.exists() else None,
        },
        "runtime": {
            "cmdline_has_kernel_arg": wanted_arg in cmdline,
            "live_edid_path": str(live) if live is not None else None,
            "live_edid_sha256": sha256_file(live) if live is not None and live.exists() else None,
        },
    }

    if include_kscreen:
        output = kscreen_output(connector)
        if output is None:
            payload["kscreen"] = None
        else:
            payload["kscreen"] = {
                "currentModeId": output.get("currentModeId"),
                "hdr": output.get("hdr"),
                "wcg": output.get("wcg"),
                "vrrPolicy": output.get("vrrPolicy"),
            }

    return payload


def verification_failures(status: dict[str, Any]) -> list[str]:
    """Return human-readable verification failures for a collected status."""

    failures: list[str] = []
    if status["package"]["sha256"] != PATCHED_SHA256:
        failures.append("packaged patched EDID checksum does not match the expected value")
    if status["installed"]["sha256"] != PATCHED_SHA256:
        failures.append("installed firmware EDID is missing or has the wrong checksum")
    if not status["runtime"]["cmdline_has_kernel_arg"]:
        failures.append("kernel command line does not include the EDID override argument")
    if status["runtime"]["live_edid_sha256"] != PATCHED_SHA256:
        failures.append("live DRM EDID does not match the patched EDID; reboot may be required")
    return failures


def print_human_status(status: dict[str, Any], *, verify: bool = False) -> None:
    """Print a scan-friendly text status."""

    print(f"Connector: {status['connector']}")
    print(f"Kernel arg: {status['kernel_arg']}")
    print(
        "Packaged EDID: "
        f"{status['package']['sha256']} "
        f"({'OK' if status['package']['sha256'] == PATCHED_SHA256 else 'BAD'})"
    )
    print(
        "Installed EDID: "
        f"{status['installed']['path']} "
        f"{status['installed']['sha256'] or 'missing'}"
    )
    print(f"Kernel arg active: {status['runtime']['cmdline_has_kernel_arg']}")
    print(
        "Live DRM EDID: "
        f"{status['runtime']['live_edid_path'] or 'not found'} "
        f"{status['runtime']['live_edid_sha256'] or 'missing'}"
    )

    if status.get("kscreen") is not None:
        kscreen = status["kscreen"]
        print(
            "KScreen: "
            f"mode={kscreen['currentModeId']} "
            f"hdr={kscreen['hdr']} "
            f"wcg={kscreen['wcg']} "
            f"vrrPolicy={kscreen['vrrPolicy']}"
        )

    if verify:
        failures = verification_failures(status)
        if failures:
            print("Verification: FAIL")
            for failure in failures:
                print(f"- {failure}")
        else:
            print("Verification: PASS")


def require_root(dry_run: bool) -> None:
    """Fail early when an install/remove operation needs privileges."""

    if not dry_run and hasattr(os, "geteuid") and os.geteuid() != 0:
        raise SystemExit("This command must run as root. Use sudo, or pass --dry-run.")


def run_system_command(cmd: list[str], *, dry_run: bool) -> None:
    """Run or print a root-side system command."""

    if dry_run:
        print("+ " + " ".join(cmd))
        return
    subprocess.run(cmd, check=True)


def install_override(args: argparse.Namespace) -> int:
    """Install the packaged EDID override into firmware and GRUB config."""

    require_root(args.dry_run)
    data = package_edid_bytes()
    actual_sha = sha256_bytes(data)
    if actual_sha != PATCHED_SHA256:
        raise SystemExit(f"Refusing to install unexpected packaged EDID checksum {actual_sha}")

    dest = firmware_dest(args.firmware_root)
    wanted_arg = kernel_arg(args.connector)

    print(f"Installing EDID override: {dest}")
    print(f"Kernel arg: {wanted_arg}")
    if args.dry_run:
        print(f"+ install -d -m 0755 {dest.parent}")
        print(f"+ install -m 0644 packaged:{PATCHED_EDID} {dest}")
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        dest.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)

    run_system_command(["grubby", "--update-kernel=ALL", f"--args={wanted_arg}"], dry_run=args.dry_run)
    if not args.skip_dracut:
        run_system_command(["dracut", "--force"], dry_run=args.dry_run)

    print("Reboot is required before the patched EDID is active.")
    return 0


def remove_override(args: argparse.Namespace) -> int:
    """Remove the firmware EDID override and GRUB config."""

    require_root(args.dry_run)
    dest = firmware_dest(args.firmware_root)
    wanted_arg = kernel_arg(args.connector)

    print(f"Removing EDID override: {dest}")
    print(f"Kernel arg: {wanted_arg}")
    run_system_command(["grubby", "--update-kernel=ALL", f"--remove-args={wanted_arg}"], dry_run=args.dry_run)
    if args.dry_run:
        print(f"+ rm -f {dest}")
    else:
        dest.unlink(missing_ok=True)
    if not args.skip_dracut:
        run_system_command(["dracut", "--force"], dry_run=args.dry_run)

    print("Reboot is required to return to the TV-provided EDID.")
    return 0


def status_command(args: argparse.Namespace) -> int:
    """Print current EDID override status."""

    status = collect_status(
        connector=args.connector,
        firmware_root=args.firmware_root,
        proc_cmdline=args.proc_cmdline,
        sysfs_drm=args.sysfs_drm,
        include_kscreen=not args.no_kscreen,
    )
    if args.json:
        print(json.dumps(status, indent=2, sort_keys=True))
    else:
        print_human_status(status)
    return 0


def verify_command(args: argparse.Namespace) -> int:
    """Verify the EDID override is installed and active."""

    status = collect_status(
        connector=args.connector,
        firmware_root=args.firmware_root,
        proc_cmdline=args.proc_cmdline,
        sysfs_drm=args.sysfs_drm,
        include_kscreen=not args.no_kscreen,
    )
    if args.json:
        payload = {"status": status, "failures": verification_failures(status)}
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print_human_status(status, verify=True)
    return 1 if verification_failures(status) else 0


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add path/configuration arguments shared by subcommands."""

    parser.add_argument("--connector", default=DEFAULT_CONNECTOR, help=f"DRM connector. Default: {DEFAULT_CONNECTOR}")
    parser.add_argument(
        "--firmware-root",
        type=Path,
        default=DEFAULT_FIRMWARE_ROOT,
        help=f"Firmware root. Default: {DEFAULT_FIRMWARE_ROOT}",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(description="Manage the LG HDR 119 Hz EDID override.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install the patched EDID override.")
    add_common_args(install_parser)
    install_parser.add_argument("--dry-run", action="store_true", help="Print changes without writing them.")
    install_parser.add_argument("--skip-dracut", action="store_true", help="Do not rebuild initramfs.")
    install_parser.set_defaults(func=install_override)

    remove_parser = subparsers.add_parser("remove", help="Remove the patched EDID override.")
    add_common_args(remove_parser)
    remove_parser.add_argument("--dry-run", action="store_true", help="Print changes without writing them.")
    remove_parser.add_argument("--skip-dracut", action="store_true", help="Do not rebuild initramfs.")
    remove_parser.set_defaults(func=remove_override)

    status_parser = subparsers.add_parser("status", help="Print install/runtime status.")
    add_common_args(status_parser)
    status_parser.add_argument("--proc-cmdline", type=Path, default=DEFAULT_PROC_CMDLINE)
    status_parser.add_argument("--sysfs-drm", type=Path, default=DEFAULT_SYSFS_DRM)
    status_parser.add_argument("--no-kscreen", action="store_true", help="Skip kscreen-doctor probing.")
    status_parser.add_argument("--json", action="store_true", help="Print JSON.")
    status_parser.set_defaults(func=status_command)

    verify_parser = subparsers.add_parser("verify", help="Verify the override is installed and active.")
    add_common_args(verify_parser)
    verify_parser.add_argument("--proc-cmdline", type=Path, default=DEFAULT_PROC_CMDLINE)
    verify_parser.add_argument("--sysfs-drm", type=Path, default=DEFAULT_SYSFS_DRM)
    verify_parser.add_argument("--no-kscreen", action="store_true", help="Skip kscreen-doctor probing.")
    verify_parser.add_argument("--json", action="store_true", help="Print JSON.")
    verify_parser.set_defaults(func=verify_command)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Run the EDID management CLI."""

    args = parse_args(argv)
    try:
        raise SystemExit(args.func(args))
    except subprocess.CalledProcessError as err:
        print(f"Command failed: {' '.join(err.cmd)}", file=sys.stderr)
        raise SystemExit(err.returncode)


if __name__ == "__main__":
    main()
