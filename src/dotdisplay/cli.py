"""Command line entry point.

One console script with subcommands rather than several scripts:

    dotdisplay daemon                     run the board
    dotdisplay status --name N --state S  report a session (used by hooks)
    dotdisplay send IMAGE                 push one image to the panel
"""

import argparse
import json
import re
import sys

from dotdisplay import __version__

SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
STATES = ("running", "question", "issue", "done")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotdisplay",
        description="Show your Claude Code sessions on an iDotMatrix LED panel.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("daemon", help="run the board")

    status = sub.add_parser("status", help="report this session's state")
    status.add_argument("--name", required=True)
    status.add_argument("--state", choices=STATES)
    status.add_argument("--left", type=int, help="stages remaining")
    status.add_argument("--clear", action="store_true",
                        help="deregister this session")

    send = sub.add_parser("send", help="push one image to the panel")
    send.add_argument("image")

    return parser


def _cmd_status(args) -> int:
    """Write one session file.

    Called from hooks on every prompt, so it must fail quietly and never
    block a session.
    """
    from dotdisplay.config import Config

    if not SAFE_NAME.match(args.name):
        # The name becomes a filename and is rendered to an image; it must
        # not carry path separators or control bytes.
        print(f"invalid session name: {args.name!r}", file=sys.stderr)
        return 1

    directory = Config.from_env().state_dir
    path = directory / f"{args.name}.json"

    if args.clear:
        try:
            path.unlink(missing_ok=True)   # hooks fire for unknown sessions
        except OSError as exc:
            print(f"dotdisplay: {exc}", file=sys.stderr)
            return 1
        return 0

    if not args.state:
        print("--state is required unless --clear is given", file=sys.stderr)
        return 1

    body = {"name": args.name, "status": args.state}
    if args.left is not None:
        body["stages_left"] = args.left
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(body))
    except OSError as exc:
        print(f"dotdisplay: {exc}", file=sys.stderr)
        return 1
    return 0


def _cmd_daemon() -> int:
    import asyncio
    import logging

    from dotdisplay import daemon
    from dotdisplay.config import Config

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    return asyncio.run(daemon.run(Config.from_env())) or 0


def _cmd_send(args) -> int:
    import asyncio

    from PIL import Image

    from dotdisplay.ble import PanelClient
    from dotdisplay.config import Config

    config = Config.from_env()
    if not config.mac:
        print("DOTDISPLAY_MAC is required", file=sys.stderr)
        return 1

    async def go():
        with Image.open(args.image) as img:
            async with PanelClient(config.mac) as panel:
                await panel.send_image(img)

    try:
        asyncio.run(go())
    except (OSError, ValueError) as exc:
        print(f"dotdisplay: {exc}", file=sys.stderr)
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return _cmd_status(args)
    if args.command == "daemon":
        return _cmd_daemon()
    if args.command == "send":
        return _cmd_send(args)

    # No subcommand is not success. print_usage() defaults to stdout, but a
    # failure path belongs on stderr.
    parser.print_usage(sys.stderr)
    return 1
