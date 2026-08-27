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
QUEUE_TIMEOUT_S = 20


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotdisplay",
        description="Show your Claude Code sessions on an iDotMatrix LED panel.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--json", action="store_true",
                        help="print one JSON object instead of prose")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("daemon", help="run the board")

    status = sub.add_parser("status", help="report this session's state")
    name_or_this = status.add_mutually_exclusive_group(required=True)
    name_or_this.add_argument("--name")
    name_or_this.add_argument("--this", action="store_true",
                              help="use the name the hooks recorded for this "
                                   "directory")
    status.add_argument("--state", choices=STATES)
    status.add_argument("--left", type=int, help="stages remaining")
    status.add_argument("--clear", action="store_true",
                        help="deregister this session")

    send = sub.add_parser("send", help="push one image to the panel")
    send.add_argument("image")

    sub.add_parser("discover", help="scan for iDotMatrix panels in range")

    text = sub.add_parser("text", help="show text on the panel")
    text.add_argument("text")
    text.add_argument("--colour", help="six hex digits, default white")

    bright = sub.add_parser("brightness", help="set brightness 0-100")
    bright.add_argument("percent", type=int)

    power = sub.add_parser("power", help="turn the panel on or off")
    power.add_argument("state", choices=["on", "off"])

    pixel = sub.add_parser("pixel", help="light one pixel")
    pixel.add_argument("x", type=int)
    pixel.add_argument("y", type=int)
    pixel.add_argument("colour")

    fill = sub.add_parser("fill", help="fill the panel with one colour")
    fill.add_argument("colour")

    sub.add_parser("clear", help="blank the panel")

    board = sub.add_parser("board", help="show the board in this terminal")
    board.add_argument("--watch", action="store_true", help="redraw until Ctrl-C")
    board.add_argument("--no-colour", action="store_true")

    sub.add_parser("statusline",
                   help="one short line for a prompt or status bar")

    check = sub.add_parser(
        "check", help="show a code on the panel to confirm the address")
    check.add_argument("--code", help="use a fixed code instead of a random one")

    return parser


def _resolve_this(config) -> str | None:
    """Read the session name the hooks recorded for the current directory.

    The assistant cannot derive its own name: it comes from the hook payload,
    which the assistant never sees. The hooks leave it here instead.

    Known limit: two sessions in the SAME directory share one pointer, so the
    most recently prompted one wins. In practice the assistant reports right
    after being prompted, which is that same session.
    """
    import os
    import re
    slug = re.sub(r"[^A-Za-z0-9._-]+", "", os.getcwd().replace("/", "_")) or "root"
    try:
        return (config.state_dir.parent / "current" / f"{slug}.name").read_text().strip()
    except OSError:
        return None


def _cmd_status(args) -> int:
    """Write one session file.

    Called from hooks on every prompt, so it must fail quietly and never
    block a session.
    """
    from dotdisplay.config import Config

    config = Config.from_env()
    if getattr(args, "this", False):
        resolved = _resolve_this(config)
        if not resolved:
            print("no session recorded for this directory; is the plugin "
                  "installed and has a prompt been sent?", file=sys.stderr)
            return 1
        args.name = resolved

    if not SAFE_NAME.match(args.name):
        # The name becomes a filename and is rendered to an image; it must
        # not carry path separators or control bytes.
        print(f"invalid session name: {args.name!r}", file=sys.stderr)
        return 1

    directory = config.state_dir
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


def _cmd_discover() -> int:
    """List panels in range.

    The installer asks for a Bluetooth address, which a first-time user has
    no way to know: `bluetoothctl devices` only lists adapters that have
    already seen the panel, so it is empty exactly when help is needed most.
    """
    import asyncio

    from bleak import BleakScanner

    async def scan():
        return await BleakScanner.discover(timeout=8.0)

    try:
        devices = asyncio.run(scan())
    except Exception as exc:      # noqa: BLE001 - report, do not traceback
        print(f"dotdisplay: could not scan: {exc}", file=sys.stderr)
        return 1

    panels = [d for d in devices if (d.name or "").upper().startswith("IDM")]
    if not panels:
        print("No iDotMatrix panel found. It advertises as IDM-<six hex "
              "digits>; check that it is powered on and in range.",
              file=sys.stderr)
        return 1

    for device in panels:
        print(f"{device.address}  {device.name}")
    print("\nUse one of these as DOTDISPLAY_MAC.", file=sys.stderr)
    return 0


def _cmd_check(args) -> int:
    """Show a code on the panel.

    Reachability alone proves only that *something* answered. Seeing the code
    with your own eyes is what proves the address points at the panel you are
    looking at -- which matters as soon as there is more than one.
    """
    import asyncio
    import secrets

    from dotdisplay import render
    from dotdisplay.ble import PanelClient
    from dotdisplay.config import Config

    config = Config.from_env()
    if not config.mac:
        print("DOTDISPLAY_MAC is not set. Run 'dotdisplay discover' to find "
              "the panel's address.", file=sys.stderr)
        return 1

    code = args.code or f"{secrets.randbelow(10000):04d}"

    async def go():
        async with PanelClient(config.mac) as panel:
            await panel.send_image(render.render_code(code))

    try:
        asyncio.run(go())
    except Exception as exc:      # noqa: BLE001 - report, do not traceback
        print(f"dotdisplay: could not reach {config.mac}: {exc}",
              file=sys.stderr)
        print("If the board daemon is running it already owns the radio; "
              "stop it first:\n  systemctl --user stop dotdisplay.service",
              file=sys.stderr)
        return 1

    print(f"Connected to {config.mac}.")
    print(f"The panel should now be showing:  {code}")
    print("If you see that code, the address is correct.")
    return 0


def _run_command(command: dict, as_json: bool) -> int:
    """Queue it if the daemon holds the radio, otherwise connect directly.

    A script should not have to know which of those is the case -- that is
    the entire reason the local queue exists.
    """
    import asyncio

    from dotdisplay import commands, queue
    from dotdisplay.ble import PanelClient
    from dotdisplay.config import Config

    config = Config.from_env()
    if not config.mac:
        print("DOTDISPLAY_MAC is not set. Run 'dotdisplay discover'.",
              file=sys.stderr)
        return 1

    async def direct():
        async with PanelClient(config.mac) as panel:
            return await commands.execute(panel, command)

    result = None
    if queue.daemon_is_alive(config):
        request_id = queue.submit(config, command)
        result = queue.await_result(config, request_id, timeout_s=QUEUE_TIMEOUT_S)
        if result is None:
            # The heartbeat can outlive a crashed daemon. Rather than fail,
            # try the radio ourselves; if it really is held, the direct
            # attempt reports that accurately.
            print("board daemon did not answer; connecting directly",
                  file=sys.stderr)

    if result is None:
        try:
            result = {"status": "done", "result": asyncio.run(direct())}
        except Exception as exc:      # noqa: BLE001 - report, no traceback
            result = {"status": "error", "message": str(exc)}

    if as_json:
        print(json.dumps(result))
    elif result.get("status") == "error":
        print(f"dotdisplay: {result.get('message')}", file=sys.stderr)
    return 0 if result.get("status") == "done" else 1


_PANEL_COMMANDS = {
    "text": lambda a: {"type": "text", "text": a.text, "colour": a.colour},
    "brightness": lambda a: {"type": "brightness", "percent": a.percent},
    "power": lambda a: {"type": "power", "on": a.state == "on"},
    "pixel": lambda a: {"type": "pixel", "x": a.x, "y": a.y, "colour": a.colour},
    "fill": lambda a: {"type": "fill", "colour": a.colour},
    "clear": lambda a: {"type": "clear"},
}


def _cmd_board(args) -> int:
    """Show the board as text.

    Needs no panel, no MAC and no daemon: this is the path for everyone who
    does not own the hardware.
    """
    import time

    from dotdisplay import sources
    from dotdisplay.config import Config
    from dotdisplay.presenters import text as presenter

    config = Config.from_env()
    colour = not args.no_colour and sys.stdout.isatty()

    def once():
        sessions = sources.read_local_sessions(config)
        stats = ({} if sessions
                 else sources.ccusage_stats(config, sources.CcusageCache()))
        out = presenter.board(sessions, sources.read_header(), stats)
        if colour:
            for state, code in presenter.STATE_COLOURS.items():
                word = presenter.STATE_WORDS[state]
                out = out.replace(word, f"\033[38;5;{code}m{word}\033[0m")
        print(out)

    if not args.watch:
        once()
        return 0

    try:
        while True:
            print("\033[2J\033[H", end="")
            once()
            time.sleep(config.poll_s)
    except KeyboardInterrupt:
        return 0


def _cmd_statusline() -> int:
    """Print one segment for a prompt.

    Reads files and nothing else: this runs on every prompt render, so it
    must never touch the radio or block.
    """
    from dotdisplay import sources
    from dotdisplay.config import Config
    from dotdisplay.presenters import text as presenter

    line = presenter.statusline(sources.read_local_sessions(Config.from_env()))
    if line:
        print(line)
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
    if args.command == "discover":
        return _cmd_discover()
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "board":
        return _cmd_board(args)
    if args.command == "statusline":
        return _cmd_statusline()
    if args.command in _PANEL_COMMANDS:
        return _run_command(_PANEL_COMMANDS[args.command](args), args.json)

    # No subcommand is not success. print_usage() defaults to stdout, but a
    # failure path belongs on stderr.
    parser.print_usage(sys.stderr)
    return 1
