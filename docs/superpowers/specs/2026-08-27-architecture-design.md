# claude-dot-display — Architecture Design

**Status:** approved 2026-08-27
**Supersedes:** `hwmon/docs/superpowers/specs/2026-08-27-session-overview-display-design.md`
and its implementation plan, which designed this as a feature inside `sensmonlight`.

## 1. What this is

An MIT-licensed tool that turns a 64x64 iDotMatrix LED panel into an ambient
status board for Claude Code sessions, built on top of an original,
clean-room implementation of the iDotMatrix BLE protocol.

It serves two audiences on purpose:

- people who want the session board, and
- people who only want a permissively-licensed iDotMatrix driver.

The second audience is the reason the driver is a real boundary rather than an
implementation detail.

## 2. Why it is a separate project

The board began as a feature inside `sensmonlight` (see the superseded spec).
Three things moved it out.

**The licence.** Every existing Python iDotMatrix library is GPL-3.0 —
verified 2026-08-27 against the GitHub API:

| Project | Licence |
| --- | --- |
| `derkalle4/python3-idotmatrix-client` | GPL-3.0 |
| `markusressel/idotmatrix-api-client` | GPL-3.0 |
| `python3-idotmatrix-library` (upstream of the above) | GPL-3.0 |

`sensmonlight` currently depends on the second of these, pinned to an unpinned
git HEAD. Internally that triggers no obligation, because GPL duties attach to
distribution and hwmon is never distributed. But it makes a permissively
licensed public release impossible: a project cannot meaningfully call itself
MIT while requiring a GPL-3.0 library at runtime.

**The dependency.** Tracking a git HEAD for a device driver is a standing
liability that has already been flagged as a follow-up twice.

**The audience.** A session board that only works if you also run
hwmon-server is not something a stranger can adopt.

## 3. Goals

- A working, dependency-free MIT driver for the 64x64 iDotMatrix panel.
- A protocol document whose provenance is defensible opcode by opcode.
- A local-first session board: install, run one command, see your sessions.
- A Claude Code plugin that makes reporting automatic.
- hwmon keeps working, with its GPL dependency removed.

## 4. Non-goals

- Supporting iDotMatrix models we cannot test. Claims are limited to hardware
  we have verified against.
- A pluggable framework for arbitrary data sources or display backends. There
  is one source type and one display; abstraction waits for a second real case.
- Replacing hwmon-server. It keeps its endpoints and its dashboard.

## 5. Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| Structure | One repo, layered: driver package + board on top | Clean internal boundary without paying for two repos, two CIs, two release flows |
| Topology | Local-first, optional remote source | A server dependency kills open-source adoption; the optional source preserves the multi-host case |
| hwmon | Daemon absorbs the agent role; iDotMatrix code deleted from `sensmonlight` | Only one process may own the radio; two agents would contend |
| Protocol | Clean-room, from observed wire traffic only | Makes the MIT claim true rather than asserted |
| Name | Repo/plugin/PyPI `claude-dot-display`; command and module `dotdisplay` | Discoverable and unclaimed, but short to type |
| Logo | Author-supplied artwork; a cropped detail doubles as the panel splash | Keeps the project's visual identity in the author's hands; the splash reuses it rather than inventing a second mark |
| Plugin | Hooks + skill + setup command | Hooks cannot drift; the skill supplies what only the model knows |

### Name availability (verified 2026-08-27)

- PyPI `claude-dot-display`: unclaimed (404).
- GitHub `claude-dot-display`: no repositories with that name.
- Plain `dotdisplay` was rejected for the public name: GitHub search is
  dominated by Google Pixel Watch tooling.

## 6. Architecture

```
   Claude Code sessions              hwmon-server
   (hooks + skill)                   /idotmatrix/commands
          |  write JSON                     |  poll (optional)
          v                                 |
   ~/.local/state/dotdisplay/sessions/      |
          |  watch                          |
          v                                 v
       +---------------------------------------+
       |            dotdisplay daemon          |   sole owner of the radio
       |    sources -> board -> render -> ble  |
       +---------------------------------------+
                          |
                         BLE
                          v
                     64x64 panel
```

Four layers, each independently testable:

| Layer | Responsibility | Depends on |
| --- | --- | --- |
| `dotdisplay.ble` | Wire protocol: frames, opcodes, pacing, connection. Knows nothing about sessions. | `bleak` |
| `dotdisplay.render` | Pure: state to a 64x64 PNG. No I/O. | Pillow |
| `dotdisplay.sources` | Where session state comes from: local files, optional HTTP | `requests` |
| `dotdisplay.daemon` | Poll sources, render, send on change | the three above |

The boundary that matters is `ble` against everything else. Nothing above it
may know a byte of protocol. That is what makes the driver independently
publishable and independently testable against a fake transport.

## 7. Session state

Hooks write one JSON file per session:

```
~/.local/state/dotdisplay/sessions/<name>.json
```

```json
{
  "name": "hwmon-d7",
  "status": "running",
  "stages_left": 3,
  "updated_at": "2026-08-27T15:04:11+02:00"
}
```

`status` is one of `running`, `question`, `issue`, `done`. `stages_left` is
optional, 0-99. `name` matches `^[A-Za-z0-9._-]{1,32}$` and is truncated to
nine characters for display.

Files, not a socket or a port: no server, no secret, and no ordering problem
where a hook fires before the daemon is up. A session killed without its
`SessionEnd` hook leaves a stale file, which the daemon ages out by mtime.

**Optional remote source.** The daemon can additionally poll an HTTP endpoint
for sessions on other hosts. Off by default, one configuration line to enable.

## 8. hwmon integration

The daemon absorbs the role of `sensmonlight-idotmatrix-agent`: alongside
session state it polls hwmon-server's existing
`/api/sensmonlight/idotmatrix/commands` queue and executes those commands on
the same radio.

**Nothing on hwmon-server changes.** Endpoints, dashboard, and `idot-send.sh`
keep working unmodified.

In `sensmonlight`, once the daemon is verified on the panel, these are removed:

- `src/sensmonlight/idotmatrix_control.py`
- `src/sensmonlight/idotmatrix_agent.py`
- their tests
- the `idotmatrix-api-client` dependency (the GPL one)
- the agent's console script and its systemd unit

The two migrations are coupled by the radio: the old agent must be stopped
before the new daemon starts, and both must not run at once.

**Arbitration.** The daemon is single-threaded with respect to the radio and
never interleaves two operations on one connection. Queued hwmon commands take
priority over a session render, because a command is an explicit human request
while a render is ambient.

After a command runs, the daemon **keeps** its cached frame rather than
invalidating it. Invalidating would make the next tick re-send an unchanged
board and wipe the image within seconds. Because the daemon only sends when
its *render* changes — not when the panel's contents change — an
`idot-send.sh` image stays up until session state actually moves, and the
board reclaims the panel naturally at that point.

## 9. Protocol derivation — the clean-room rule

The MIT claim depends on method, so the method is a requirement, not a
preference.

**Permitted:**

- Observing bytes on the wire, from any source.
- Treating the GPL library as a black box: run a command through it, capture
  what the device receives, record those bytes as facts about the device.
- Capturing the vendor phone application talking to the panel.
- Reading the Bluetooth specification and public GATT documentation.

**Forbidden:**

- Reading GPL source code, constant tables, or comments as a source for our
  implementation.
- Copying structure or naming from a GPL implementation.

Protocol facts are not copyrightable; provenance is what makes that
defensible. `PROTOCOL.md` therefore records, per opcode: what was observed,
where it was captured, and that it was replayed successfully against real
hardware.

### Capture sources

| Source | Availability | Yields |
| --- | --- | --- |
| `btmon` on marcohp | Available now | Every opcode the existing library uses |
| Vendor app on a Galaxy A7 | Opportunistic | Opcodes no open library has, including working image upload |

The Android capture is an accelerator, never a dependency. `btmon` alone is
sufficient for a complete driver of everything we use today.

The vendor application demonstrably performs fast image upload on this exact
unit, which proves the firmware supports it. That converts an open research
question into a decoding problem.

### Why the choreographed capture matters

Capture is cheap; decoding is expensive. Actions are therefore driven one at a
time with pauses between them, and two captures are deliberately chosen to
differ by a single pixel: their byte streams then differ in almost exactly one
place, which reveals pixel ordering and packing by comparison rather than by
inference. See `docs/capture-checklist.md`.

## 10. Error handling

The daemon runs unattended. It never exits on a recoverable fault.

| Fault | Response |
| --- | --- |
| Panel out of range, asleep, unpaired | Log at debug, retry next tick |
| Source unreachable | Keep last known state; never blank the board |
| Malformed session file | Skip that file, not the loop |
| hwmon-server unreachable | Continue with local sessions only |
| Hook cannot reach state directory | Exit quietly non-zero; never block the session |

Hooks run inline in a Claude Code session, so they carry a short timeout and
must never break a session over a status update.

## 11. Testing

| Layer | Approach |
| --- | --- |
| `ble` | Fake transport capturing emitted bytes; assert exact frames against captured golden traffic |
| `render` | Byte-identical determinism, sorting, overflow, colour bands; no hardware |
| `sources` | `tmp_path` for local files; mocked HTTP for remote |
| `daemon` | Mocked layers; assert send-on-change and fault tolerance |
| plugin | `claude plugin eval` suite in CI |

No real network calls in tests. Anything that claims to render is additionally
verified on the physical panel with the C930e camera; a passing test is not
evidence that the panel changed.

## 12. Display layout

Layout constants were measured on the physical panel during the 2026-08-27
hardware session and carry forward unchanged. They are measurements, not
preferences:

- Font 8px (DejaVuSansMono-Bold); rows 9px apart; left margin x=2; right edge x=62.
- `ImageDraw.fontmode = "1"` on every draw context. Antialiased text is
  unreadable on an LED matrix.
- Never render a `%` glyph; unresolvable at 8px.
- Arrows are polygons, never font glyphs.
- Status colours: issue `(255,30,30)`, question `(255,215,0)`, done
  `(0,230,80)`, running `(60,120,255)`. Never grey, which renders as
  washed-out lavender.
- Ordering is pure alphabetical by session name, so rows stay stable.

Two screens: the session board, and an idle screen of token statistics when no
session is running. Both share a header showing the account-wide five-hour
window.

## 13. Packaging and runtime

- Python 3.11+, matching what the target hosts already run.
- Runtime dependencies: `bleak`, `pillow`, `requests`. Nothing else, and
  nothing copyleft.
- Distributed as `claude-dot-display` on PyPI; the intended install is
  `pipx install claude-dot-display`, which keeps the daemon isolated from
  system Python.
- Ships one console script, `dotdisplay`, with subcommands (`daemon`,
  `status`, `send`), rather than several scripts.
- Runs as a systemd **user** service, not a system unit: it needs the user's
  session state directory and no privileges.

## 14. Public shell

- Remotes: `git@github.com:MrMarco74/claude_dot_display.git` and
  `git@<internal-gitlab>:apps/claude_dot_display.git`.
- MIT licence.
- Author-supplied logo (`assets/logo.jpg`) with derived sizes. `assets/splash-64.png`
  is a tight crop of its glowing star, quantized to 24 colours, reused as the
  daemon's splash screen on the panel. The full logo is unreadable at 64x64;
  this was rendered and confirmed, not assumed.
- **Open:** the logo includes GitHub's Octocat, whose use GitHub's brand
  guidelines restrict. To be resolved before the first public push.
- README badges: CI, licence, Python versions, PyPI.
- GitHub Actions: lint, tests, `claude plugin eval`.
- `.claude-plugin/marketplace.json` in this repo, so
  `/plugin marketplace add MrMarco74/claude_dot_display` works on day one, and
  identically from the internal GitLab.

### README: "Why we wrote our own protocol layer"

A dedicated section stating plainly that every existing Python iDotMatrix
library is GPL-3.0, that this is incompatible with shipping genuinely
permissive software, and that the protocol here was therefore derived
clean-room from observed wire traffic against real hardware rather than from
GPL source. It links to `PROTOCOL.md` for per-opcode provenance.

The tone is factual, not disparaging: the GPL projects did the original work
of making these panels usable at all, and the section says so.

## 15. Sub-projects

This is too large for one plan. Each phase gets its own spec where needed, its
own plan, and its own implementation cycle.

| Phase | Deliverable | Depends on |
| --- | --- | --- |
| **P0** | Repo foundation: licence, layout, logo, README skeleton, CI, dual remotes, marketplace manifest | — |
| **P1** | BLE driver: capture rig, `PROTOCOL.md`, `dotdisplay.ble`, fake-transport tests | P0 |
| **P2** | Board and daemon: screens, renderer, sources, hwmon poller, systemd unit; `sensmonlight` cleanup | P1 |
| **P3** | Claude plugin: hooks, skill, setup command, manifests, eval suite | P2 |

P2 is specified only after P1 lands. Refresh speed is the open variable: if
the fast upload path is recovered, change-detection becomes an optimisation
rather than a load-bearing constraint, and P2's design changes accordingly.

## 16. Risks

| Risk | Mitigation |
| --- | --- |
| Protocol research is open-ended | P1 is bounded by "everything we already use, working"; the fast path is a stretch goal, not a gate |
| Galaxy A7 too old for the vendor app | `btmon` fallback yields a complete driver regardless |
| Radio contention during migration | Old agent stopped before new daemon starts; documented as an ordered step |
| Clean-room discipline slipping under time pressure | The rule is written here and provenance is recorded per opcode |

## 17. Follow-ups

- Submit to `anthropics/claude-plugins-official` after reading its
  contribution rules. Note the name leads with a trademark, which a curator
  may ask to change.
- Publish the driver to PyPI separately if a second consumer appears.
- Report stage counts automatically from the subagent ledger.
- A dashboard view of the session list in hwmon.
