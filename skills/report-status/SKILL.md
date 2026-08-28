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
that every prompt fires, so you only have to report it when it changes. See
*Counting stages* below for what may be counted, and how to retract a count.

## When to report

- **`question`** — immediately before you stop and ask the user something.
  The point is that they see it without reading the terminal.
- **`issue`** — when you hit a blocker you cannot resolve alone.
- **`done`** — when you finish. The next prompt sets the state back to
  `running` automatically, so you do not need to undo it.
- **the stage count** — whenever it changes, while you keep working:

      dotdisplay status --this --state running --left 4

  A descending number is what tells the user how much of a long task is
  left, which is the one thing a row of names cannot say. Report it as each
  stage completes.

## Counting stages

Only count stages you can enumerate before you start: the phases of a written
plan, the items on a todo list you have already built, the files in a batch
you have already listed. If you cannot say what the last stage is, you have no
count -- say nothing rather than estimate.

One count per session, for the work as a whole. A count that goes 5, 4, 3 and
then back up to 9 because you started counting something else is worse than no
count at all.

Retract with `--left 0` the moment the stages are finished or abandoned. The
count survives every later write, so an unretracted one outlives its plan and
keeps advertising a number that is no longer true.

## When not to report

- Do not report a stage for every step, tool call or file. Stages are the few
  boundaries a user would recognise, not your internal progress.
- Do not invent a count to have one. An empty column is honest; a wrong number
  is read from across the room and acted on.
- Do not report `done` for a step. Only for the work as a whole.
- Do not report a state you are unsure about. A board that is wrong is worse
  than a board that is stale: the user acts on it from across the room, where
  they cannot see the terminal to correct the impression.

## If the command fails

Never interrupt the work for it, never retry, and never let it change what
you were doing. The board is a convenience. But two failures mean different
things, and only one of them is safe to swallow.

**`dotdisplay: command not found` (exit 127)** — say so, once, in a single
line at the end of your reply:

> The `dotdisplay` command is not on PATH, so this session cannot report to
> the board. Re-run the installer, or link it:
> `ln -sfn ~/.local/share/dotdisplay/venv/bin/dotdisplay ~/.local/bin/`

This is not a transient failure. It means **no** report from **any** session
will ever land, so the board silently stays on `running` forever and the
user has no way to notice: they are looking at a display that appears to be
working. Staying quiet here is what makes it undiscoverable. Mention it once
per session, not once per report.

**Anything else** — ignore it and carry on, and do not tell the user unless
they ask. The hooks have not run in this directory yet (so there is no name
to resolve), or the daemon is down, or the panel is out of range. These are
real conditions with visible symptoms elsewhere, and none is worth fixing
mid-task.
