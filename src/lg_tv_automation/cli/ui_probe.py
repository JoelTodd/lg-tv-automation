"""CLI for TV UI exploration with screenshot capture after each action."""

from __future__ import annotations

import argparse
import asyncio
import json
import ssl
import urllib.request
from pathlib import Path

from bscpylgtv import WebOsClient

from ..config import require_tv_ip
from ..constants import DEFAULT_RETURN_APP


def parse_args() -> argparse.Namespace:
    """Build the UI probe CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Send LG webOS remote keys and save screenshots after each step."
    )
    parser.add_argument("--out-dir", required=True, help="Directory for captured screenshots.")
    parser.add_argument(
        "--return-app",
        default=DEFAULT_RETURN_APP,
        help=f"App id to relaunch before exit. Default: {DEFAULT_RETURN_APP}",
    )
    parser.add_argument(
        "--launch-app",
        help="Optional app id to launch before sending buttons, e.g. com.webos.app.gameoptimizer",
    )
    parser.add_argument(
        "--buttons",
        nargs="*",
        default=[],
        help="Button sequence to send, e.g. ADVANCE_SETTING DOWN DOWN RIGHT",
    )
    parser.add_argument("--delay", type=float, default=1.2, help="Delay after each action.")
    parser.add_argument("--before", action="store_true", help="Capture a screenshot before any action.")
    parser.add_argument(
        "--status",
        action="store_true",
        help="Save current app and input status JSON alongside screenshots.",
    )
    return parser.parse_args()


def download_screenshot(uri: str, path: Path) -> None:
    """Download a one-shot screenshot URI to disk."""

    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(uri, context=ctx, timeout=15) as response:
        path.write_bytes(response.read())


async def capture(client: WebOsClient, out_dir: Path, index: int, label: str) -> None:
    """Capture a numbered screenshot for a single automation step."""

    shot = await client.request("tv/executeOneShot", {})
    image_uri = shot["imageUri"]
    safe_label = label.lower().replace(" ", "_")
    download_screenshot(image_uri, out_dir / f"{index:02d}_{safe_label}.jpg")


async def dump_status(client: WebOsClient, out_dir: Path) -> None:
    """Persist current app and input status for offline inspection."""

    payload = {
        "current_app": await client.get_current_app(),
        "input_status": await client.request("com.webos.service.eim/getAllInputStatus", {}),
    }
    (out_dir / "status.json").write_text(json.dumps(payload, indent=2))


async def async_main() -> int:
    """Run the screenshot-driven UI probe session."""

    args = parse_args()
    tv_ip = require_tv_ip()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    client = await WebOsClient.create(tv_ip)
    await client.connect()
    try:
        if args.status:
            await dump_status(client, out_dir)

        index = 0
        if args.before:
            await capture(client, out_dir, index, "before")
            index += 1

        if args.launch_app:
            await client.launch_app(args.launch_app)
            await asyncio.sleep(args.delay)
            await capture(client, out_dir, index, f"launch_{args.launch_app}")
            index += 1

        for button in args.buttons:
            await client.button(button)
            await asyncio.sleep(args.delay)
            await capture(client, out_dir, index, button)
            index += 1

        return 0
    finally:
        try:
            if args.return_app:
                await client.launch_app(args.return_app)
                await asyncio.sleep(args.delay)
        finally:
            await client.disconnect()


def main() -> None:
    try:
        raise SystemExit(asyncio.run(async_main()))
    except RuntimeError as err:
        raise SystemExit(str(err)) from err
