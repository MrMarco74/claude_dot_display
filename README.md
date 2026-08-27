<div align="center">

<img src="assets/logo.jpg" width="320" alt="claude-dot-display">

# claude-dot-display

**Your Claude Code sessions, on an LED matrix.**

[![CI](https://github.com/MrMarco74/claude_dot_display/actions/workflows/ci.yml/badge.svg)](https://github.com/MrMarco74/claude_dot_display/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-alpha-orange.svg)](#status)

</div>

---

`claude-dot-display` turns a 64x64 iDotMatrix LED panel into an ambient status
board for your Claude Code sessions. Each running session gets a row: its name
in a colour that tells you its state, and how many stages it has left.

- **blue** — running
- **amber** — waiting on a question
- **red** — hit an issue
- **green** — done

When nothing is running, the panel switches to a summary of your token usage.

Underneath it is an original, MIT-licensed implementation of the iDotMatrix
BLE protocol, usable on its own.

## No panel? It still works

`dotdisplay board` shows the same board in your terminal, and
`dotdisplay statusline` puts a one-line summary wherever you want one. Neither
needs Bluetooth, an address, or the daemon.

```
claude-dot-display            38% · reset 22:10
───────────────────────────────────────────────
● a-very-long-session waiting on you          3
● hwmon-d7            issue                   7
● kolonial            done                    0
● storygen            running                12
```

The terminal view is not a mirror of the panel. Nine-character names, the 8px
font and the four-row budget are consequences of a 64x64 LED matrix; a
terminal has none of them, so it shows full names and spells the states out.

To put the summary in Claude Code's status line, add to your `settings.json`:

```json
{
  "statusLine": { "type": "command", "command": "dotdisplay statusline" }
}
```

It shows **names for the sessions that need you** and counts for the rest:

```
!hwmon-d7 ?storygen *3
```

`!` issue · `?` waiting on you · `*` running · `+` done. Knowing *which*
session is blocked is actionable; knowing which one is merely running is not,
so those stay a number. If names would push the line past 60 characters it
falls back to counts entirely — a prompt segment that wraps is worse than one
that is vague. `--counts-only` forces that shorter form.

It prints nothing when no session is running, so it costs you no space when
there is nothing to say. **The installer will not edit `settings.json` for
you** — you may already have a status line, and replacing it is not ours to
do. If you already run one for its side effects, wrap it rather than replace
it: mine writes the rate-limit file this board's header reads.

The LED matrix is the fun part, not the price of entry.

## Status

Alpha, and honest about it. The repository is being built in phases:

| Phase | What | State |
| --- | --- | --- |
| P0 | Repo foundation | **done** |
| P1 | BLE driver | **done** — verified against hardware |
| P2 | Board, renderer, daemon | **done** — running as a service |
| P3 | Claude Code plugin | **done** — sessions report themselves |
| P4 | Shell toolkit | **done** |
| P5 | Agent integration guide | **done** |
| P6 | Works without a panel | **done** |

Install the plugin today and it does nothing yet — it carries no hooks or
skills until P3. It exists so the install path is real and testable.

[`PROTOCOL.md`](PROTOCOL.md) is already worth reading if you own one of these
panels. Everything in it is marked with whether it has been replayed against
real hardware yet — at this stage, none of it has.

## Why we wrote our own protocol layer

Every Python library for these panels is **GPL-3.0**:

| Project | Licence |
| --- | --- |
| [`derkalle4/python3-idotmatrix-client`](https://github.com/derkalle4/python3-idotmatrix-client) | GPL-3.0 |
| [`markusressel/idotmatrix-api-client`](https://github.com/markusressel/idotmatrix-api-client) | GPL-3.0 |
| `python3-idotmatrix-library` (upstream of both) | GPL-3.0 |

Those projects did the original work of making these panels usable at all, and
this one would not exist without the path they cut. But their licence is
viral: anything that links them inherits it. A project cannot honestly call
itself MIT while requiring a GPL-3.0 library at runtime.

We wanted this to be **really** free — usable in commercial work, in
proprietary tools, in anything at all, with no obligations flowing back. So we
wrote the protocol layer ourselves.

**Clean-room, and we mean it.** No GPL source was read. The protocol was
derived from bytes observed on the wire against real hardware — the vendor's
own phone application talking to the panel, captured over Bluetooth HCI and
decoded. Protocol facts are not copyrightable, but provenance is what makes
that defensible, so [`PROTOCOL.md`](PROTOCOL.md) records for every command
where it was captured and whether it has been replayed.

### It turned out faster, too

Going to the wire directly answered a question the existing libraries had not:
how the vendor app uploads a full frame so quickly. It sends the panel raw
RGB888 in three chunks. This driver does the same in a **measured 0.77
seconds** — the per-pixel approach the GPL libraries use takes roughly **6
seconds** for the same frame.

The pixel ordering — row-major, origin top-left — was established by
uploading three images that differ from a black baseline by exactly one pixel,
at three known corners, and finding that pixel in each payload. Measured, not
inferred.

If you only want a permissively licensed way to talk to one of these panels,
take `dotdisplay.ble` and ignore the rest.

## Requirements

- Python 3.11 or newer
- A Bluetooth LE adapter within range of the panel
- A 64x64 iDotMatrix panel

**No pairing is needed** — the panel accepts connections unpaired. What you
do need is its Bluetooth address:

```bash
dotdisplay discover          # lists panels in range as IDM-<six hex digits>
dotdisplay check             # shows a code on the panel to confirm it
```

`check` matters when more than one panel is around: reachability only proves
that *something* answered, while seeing the code proves the address points at
the panel you are looking at.

Other iDotMatrix sizes are untested. We only claim what we have verified
against hardware.

## Install

```bash
pipx install claude-dot-display
```

Not yet published — see Status.

## Shell usage

Every command works **whether or not the board daemon is running**. If it is,
the command is queued and the daemon executes it; if not, the CLI connects
directly. A script does not have to know which.

```bash
dotdisplay text "BUILD OK" --colour 00ff00
dotdisplay fill 0000ff
dotdisplay pixel 0 0 ff0000
dotdisplay brightness 40
dotdisplay power off
dotdisplay clear
```

Add `--json` for one machine-readable object per command; the exit code is
`0` only when its `status` is `done`.

```bash
if make test; then dotdisplay fill 00ff00; else dotdisplay text "KAPUTT" --colour ff0000; fi
```

Text is rasterised on this side and sent as an image: the panel has no font
of its own (see [`PROTOCOL.md`](PROTOCOL.md)). The size is chosen so the text
fills as much of the panel as it can.

## For agents and LLMs

[`AGENTS.md`](AGENTS.md) is the integration contract: the commands, the
`--json` output shape, and — more usefully — the constraints that are
invisible from outside, such as the single radio owner and the fact that
nothing can read back what the panel is showing.

## Licence

MIT — see [LICENSE](LICENSE).
