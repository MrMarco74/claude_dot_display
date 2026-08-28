---
name: report-status
description: Use when you hit a question for the user, hit a blocking problem, or finish the work - reports this session's state to the claude-dot-display LED board so the user can see it from across the room.
---

# Reporting session status to the board

Hooks already report that this session is **running**. They cannot know
anything else. Three states only you can report:

| State | When |
| --- | --- |
| `question` | You are waiting on the user and cannot proceed |
| `issue` | You hit something broken or blocking |
| `done` | The work you were asked for is finished |

Report with:

    dotdisplay status --this --state question --left 3

`--this` resolves the session name the hooks recorded for this directory. You
cannot derive that name yourself — it comes from the hook payload, which you
never see — so always use `--this` rather than guessing a name.

`--left` is the number of stages still to go when you are working through a
plan with stages. Omit it when there is no meaningful count. Never invent one.

A count you report **persists** across later writes, including the `running`
that every prompt fires, so you only have to report it when it changes. That
also means it outlives the plan unless you retract it: pass `--left 0` when
the stages are finished or abandoned, or the panel keeps advertising a number
that is no longer true.

## When to report

- **`question`** — immediately before you stop and ask the user something.
  The point is that they see it without reading the terminal.
- **`issue`** — when you hit a blocker you cannot resolve alone.
- **`done`** — when you finish. The next prompt sets the state back to
  `running` automatically, so you do not need to undo it.

## When not to report

- Do not report progress in the middle of work. `running` is already correct,
  and the board is meant to be glanceable, not a progress bar.
- Do not report `done` for a step. Only for the work as a whole.
- Do not report a state you are unsure about. A board that is wrong is worse
  than a board that is stale: the user acts on it from across the room, where
  they cannot see the terminal to correct the impression.

## If the command fails

Ignore it and carry on. The board is a convenience and is never worth
interrupting the work for. Do not retry, and do not report the failure to the
user unless they ask.

A failure usually means the plugin's hooks have not run in this directory yet
(so there is no name to resolve) or the daemon is not installed. Neither is
something to fix mid-task.
