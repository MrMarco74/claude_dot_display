# P5 — Agent Integration Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any LLM or agent — not only Claude Code — drive the panel correctly on the first attempt, without reading the source.

**Architecture:** One document, `AGENTS.md`, written for a reader who cannot experiment cheaply. It states the commands, the output contract, and above all the **constraints that are invisible from the outside** and that an agent would otherwise discover by breaking something.

**Spec:** `docs/superpowers/specs/2026-08-27-architecture-design.md`
**Depends on:** P4 — the shell commands are what the document documents.

## Why a document rather than an MCP server

An MCP server gives typed calls, which is genuinely better than shell
guessing. It is deliberately **not** this phase, for two reasons:

1. Inside Claude Code the skill from P3 already covers the common case, so an
   MCP server would largely duplicate it.
2. Outside Claude Code, MCP support is uneven, while every agent that can run
   a shell command can read a file.

The document is therefore the broader win for less surface. An MCP server
remains a reasonable follow-up once there is a caller that wants one.

## Global Constraints

- **Write for someone who cannot try things cheaply.** Every failure mode an
  agent could hit must be stated, not left to be discovered.
- **State constraints, not just capabilities.** A command list without the
  radio-ownership rule produces confident, broken code.
- **No marketing.** This is a contract, not a README.
- Test: `.venv/bin/python -m pytest -q`. **Baseline 165 tests** (after P4).
- The document must be verified by an agent that has never seen this repo.

---

### Task 1: AGENTS.md

**Files:**
- Create: `AGENTS.md`
- Modify: `README.md` (link it)

- [ ] **Step 1: Write the document**

Create `AGENTS.md`. It must contain, in this order:

**What this is** — one paragraph. A 64x64 LED panel driven over BLE by the
`dotdisplay` CLI.

**Before anything works** — the preconditions, stated as checks:

```bash
dotdisplay --version                 # installed?
echo "$DOTDISPLAY_MAC"               # address configured?
dotdisplay discover                  # if not: find it
dotdisplay check                     # confirm it is the right panel
```

**The commands**, as a table: `text`, `fill`, `clear`, `pixel`, `brightness`,
`power`, `send`, `status`, `discover`, `check`, `board`, `daemon`. One line
each, with a real example.

**The output contract:**

- `--json` prints one JSON object: `{"status": "done"|"error", ...}`
- exit `0` on `done`, `1` on anything else
- human-readable output goes to stdout, diagnostics to stderr

**Constraints that are invisible from outside** — the section that earns the
document:

| Constraint | Consequence for you |
| --- | --- |
| Only one process may own the radio | Never connect directly while the daemon runs. The CLI already routes around this; do not reimplement it |
| A full frame takes ~0.77 s | Do not animate by sending frames in a loop; you will queue faster than the panel drains |
| The device has **no font** | Text is rasterised host-side into an image. There is no "scroll this string" primitive |
| The panel shows only the last thing sent | There is no read-back. You cannot ask what is displayed |
| The board reclaims the panel when session state changes | An image you send is not permanent |
| `check` needs a human | It proves the address only if someone confirms the code by eye |
| Names are truncated to 9 characters | A longer session name will not round-trip |

**What not to do** — explicit:

- Do not stop the daemon to run a command. P4 exists so you do not have to.
- Do not poll `status` in a loop to detect changes; there is nothing to poll.
- Do not assume a command succeeded because the exit code was 0 when the
  question is whether the panel *changed* — that needs eyes or a camera.

**A worked example** — five lines of shell that do something real, e.g. show
a build result:

```bash
if make test; then
    dotdisplay fill 00ff00
else
    dotdisplay text "BUILD KAPUTT" --colour ff0000
fi
```

- [ ] **Step 2: Link it**

In `README.md`, under a new **For agents and LLMs** heading, link `AGENTS.md`
in one sentence.

- [ ] **Step 3: Commit**

```bash
git add AGENTS.md README.md
git commit -m "docs: add an integration guide for agents and LLMs"
```

---

### Task 2: verify it with an agent that has never seen the repo

**This is the only real test of a document like this.** Reading it yourself
proves nothing: you already know what it means.

- [ ] **Step 1: Prepare an isolated directory**

```bash
mkdir -p /tmp/agent-check && cp AGENTS.md /tmp/agent-check/
```

Only the document. No source, no README, no repository.

- [ ] **Step 2: Ask a fresh agent to perform a task from it alone**

```bash
cd /tmp/agent-check && claude -p "Read AGENTS.md. Then make the panel show
the word TEST in green. Explain what you did and why, but ask before running
anything that changes the panel."
```

- [ ] **Step 3: Judge the result honestly**

The document passes if the agent:

- checks the preconditions before acting
- reaches `dotdisplay text "TEST" --colour 00ff00` or an equivalent
- does **not** propose stopping the daemon
- does **not** claim success without the panel being observed

Record what it got wrong. **Every mistake it makes is a defect in the
document, not in the agent** — that is the entire point of testing it this
way.

- [ ] **Step 4: Fix what the run exposed and repeat**

Amend `AGENTS.md` for each failure, then run Step 2 again in a fresh
directory. Repeat until a first attempt is correct.

- [ ] **Step 5: Commit**

```bash
git add AGENTS.md
git commit -m "docs: fix what an unfamiliar agent got wrong"
git push origin main && git push gitlab main
```

---

## Definition of done

- `AGENTS.md` exists, is linked from the README, and covers commands, the
  output contract, and the invisible constraints.
- An agent with only that file, in an empty directory, drives the panel
  correctly on its first attempt.
- Every correction that run required is committed.

## Deliberately not in P5

- An MCP server. See the reasoning above; revisit when a caller wants one.
- Language bindings. The CLI plus `--json` is the interface.
