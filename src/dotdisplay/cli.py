"""Command line entry point.

Carries only --version today. P2 adds the `daemon`, `status` and `send`
subcommands here; keeping one console script rather than several is a
deliberate choice recorded in the design.
"""

import argparse
import sys

from dotdisplay import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotdisplay",
        description="Show your Claude Code sessions on an iDotMatrix LED panel.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    del args  # no subcommands yet; P2 dispatches here
    # print_usage() defaults to stdout; this is a failure path, so it belongs
    # on stderr and must not pollute a pipeline expecting real output.
    parser.print_usage(sys.stderr)
    return 1
