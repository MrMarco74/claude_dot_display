#!/usr/bin/env python3
"""Print one release's section of CHANGELOG.md, for use as release notes.

Release notes that are retyped from the changelog go stale on the second
release, so the changelog is the single source and this reads it. Exits 1
when the version has no section: an undocumented release is caught while it
is still a red CI run, not after the tag is public.

    changelog_section.py 0.3.0 [CHANGELOG.md]
"""

import pathlib
import re
import sys

DEFAULT = pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md"


def section(text: str, version: str) -> str | None:
    """The body under `## [version]`, without its heading.

    Stops at the next release heading or at the link definitions that close
    the file -- the oldest entry has no heading after it, and reading on
    would put bare URLs in the notes.
    """
    pattern = (rf"^## \[{re.escape(version)}\][^\n]*\n"
               r"(.*?)(?=^## \[|^\[[^\]]+\]:)")
    found = re.search(pattern, text, re.S | re.M)
    return found.group(1).strip() if found else None


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[0].lstrip("v")
    path = pathlib.Path(argv[1]) if len(argv) > 1 else DEFAULT

    try:
        text = path.read_text()
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 1

    body = section(text, version)
    if not body:
        print(f"{path} has no section for {version}", file=sys.stderr)
        return 1
    print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
