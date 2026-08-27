# Driving the panel: a guide for agents

`claude-dot-display` drives a 64x64 iDotMatrix LED panel over Bluetooth LE
through one command, `dotdisplay`. This file is the contract. It is written
for a reader who cannot experiment cheaply, so it states the constraints as
plainly as the capabilities.

## Before anything works

```bash
dotdisplay --version          # installed?
echo "$DOTDISPLAY_MAC"        # panel address configured?
dotdisplay discover           # if empty: list panels in range
dotdisplay check              # show a code on the panel to confirm it
```

`discover` prints `<address>  IDM-<six hex digits>` for each panel it finds.
No pairing is needed; the panel accepts connections unpaired.

## Commands

| Command | Example |
| --- | --- |
| `text` | `dotdisplay text "BUILD OK" --colour 00ff00` |
| `fill` | `dotdisplay fill 0000ff` |
| `clear` | `dotdisplay clear` |
| `pixel` | `dotdisplay pixel 12 30 ff0000` |
| `brightness` | `dotdisplay brightness 40` |
| `power` | `dotdisplay power off` |
| `send` | `dotdisplay send picture.png` |
| `status` | `dotdisplay status --this --state question --left 3` |
| `discover` | `dotdisplay discover` |
| `check` | `dotdisplay check` |
| `daemon` | `dotdisplay daemon` — runs the session board |

Colours are always **six hex digits**, no `#`.

## Output contract

- With `--json`, exactly one JSON object is printed:
  `{"status": "done", "result": {...}}` or `{"status": "error", "message": "..."}`
- Exit code `0` if and only if `status` is `done`; `1` otherwise.
- Human-readable output goes to stdout, diagnostics to stderr.

```bash
dotdisplay --json fill 00ff00 | jq -e '.status == "done"'
```

## Constraints you cannot see from outside

These are the things that will otherwise cost you a wrong assumption.

| Constraint | What it means for you |
| --- | --- |
| **Only one process may own the radio** | Never open your own BLE connection. The CLI already routes around the daemon; do not reimplement that |
| **A full frame takes ~0.77 s** | Do not animate by sending frames in a loop. You will submit faster than the panel drains |
| **The device has no font** | Text is rasterised host-side into an image. There is no "scroll this string" primitive, and none can be added without one |
| **There is no read-back** | You cannot ask what the panel is showing. Nothing reports the current contents |
| **The board reclaims the panel** | If the session board is running, an image you send stays only until session state changes |
| **`check` needs a human** | It proves the address only if someone confirms the code by eye. Do not treat exit 0 as proof |
| **Session names are truncated to 9 characters** | A longer name will not round-trip through the display |
| **The panel is not a log** | It shows the last thing sent. Writing to it in a loop produces a blur, not a history |

## Do not

- **Do not stop the daemon to run a command.** Commands work with it running;
  that is what the local queue is for. Stopping it takes the board away from
  the user for no reason.
- **Do not poll `status` to detect change.** There is nothing to poll; state
  is written by hooks, not queried.
- **Do not report success because the exit code was 0** when the question was
  whether the panel *changed*. That needs a human or a camera. An exit code
  tells you the command was accepted, not that anything lit up.
- **Do not guess an address.** `discover` exists precisely so you do not have
  to, and a wrong address fails in a way that looks like a dead panel.

## A worked example

```bash
#!/usr/bin/env bash
# Show the build result on the panel.
set -euo pipefail

if make test >/dev/null 2>&1; then
    dotdisplay text "BUILD OK" --colour 00ff00
else
    dotdisplay text "BUILD KAPUTT" --colour ff0000
fi
```

If you need to know whether the panel accepted it:

```bash
if ! dotdisplay --json text "HELLO" | jq -e '.status == "done"' >/dev/null; then
    echo "panel unavailable; carrying on anyway" >&2
fi
```

Treating the panel as optional is usually right: it is an ambient display, and
no task should fail because a decoration was unreachable.
