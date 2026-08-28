# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-08-28

### Fixed

- A reported stage count no longer disappears on the next prompt. `stages_left`
  had one writer and two destroyers: both the hook and the CLI rebuilt the
  session file from scratch, so the count survived only until `UserPromptSubmit`
  fired `running` over it. In practice the number the board is built to show was
  almost never on screen. Overwriting is right for the status and wrong for the
  count — a prompt means running again, but says nothing about how many stages
  remain — so both writers now carry an existing count forward when the caller
  does not supply one. An unreadable session file drops the count rather than
  keeping the session off the board.

### Changed

- `dotdisplay status --left 0` now retracts the stage count instead of storing a
  zero. A count that survives every write also outlives the plan that set it, and
  a confidently wrong number on a glanceable panel is worse than a blank column.
  On a 64x64 display "no stages left" and "no count" read identically.

## [0.2.0] - 2026-08-28

### Added

- A `PostToolUse` heartbeat keeps working sessions on the board. Session files
  were written only on `SessionStart`, `UserPromptSubmit` and `SessionEnd`, and
  freshness is judged by mtime against `stale_after_s` — so the board really
  asked "did you type in the last 15 minutes?" rather than "is this session
  alive?", and a session grinding through one long task, exactly the one worth
  watching from across the room, dropped off. `PostToolUse` is the only event
  that fires while Claude is working. It runs `report.py --beat`, which refreshes
  the timestamp and nothing else: a heartbeat asserts that a session exists,
  never what state it is in, so an amber `question` or red `issue` survives it.
  A missing file is created as `running`, so a session that predates the plugin
  still reaches the board. The hook is asynchronous, since `PostToolUse` sits on
  the critical path of every tool call.

### Fixed

- The daemon reconnects when the panel stops accepting writes. Observed on
  hardware: BlueZ dropped the resolved GATT services under a live connection and
  every write raised `Service Discovery has not been performed yet` for three
  hours, while the panel kept showing a frame that was hours old. `run()` had a
  working reconnect loop the whole time, but `tick()` caught every send failure
  and returned `False`, so the error never reached it — and a retry against a
  client whose services are gone can never succeed. `tick()` now counts
  consecutive failures and raises `PanelUnreachable` at `MAX_SEND_FAILURES`,
  handing the connection back to `run()`. The count clears on any successful
  write, so an occasional failed write still costs nothing but the next tick.
- The systemd unit puts `~/.local/bin` on the daemon's `PATH`. A user service
  does not inherit the login shell's `PATH`, so the daemon could not find
  `ccusage`: the idle screen silently lost all five token rows while the log
  filled with `ccusage unavailable` every five seconds.

## [0.1.0] - 2026-08-27

Initial release.

### Added

- An original, MIT-licensed implementation of the iDotMatrix BLE protocol,
  documented in `PROTOCOL.md` and usable on its own. Frames are split to the
  negotiated ATT MTU and paced, and the whole driver is testable through a fake
  transport without a panel, a radio or Bluetooth.
- A daemon that renders Claude Code sessions to a 64x64 panel: one row per
  session, coloured by state (blue running, amber question, red issue, green
  done), falling back to a token-usage summary when nothing is running.
- Claude Code plugin: hooks that report session state, and a `report-status`
  skill for the states only the assistant can know.
- `dotdisplay board` for the same board in a terminal and `dotdisplay statusline`
  for a one-line summary, neither of which needs Bluetooth or the daemon.
- A command queue so one-shot commands still work while the daemon owns the
  radio, a systemd user unit, and an installer.

[0.3.0]: https://github.com/MrMarco74/claude_dot_display/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MrMarco74/claude_dot_display/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/MrMarco74/claude_dot_display/releases/tag/v0.1.0
