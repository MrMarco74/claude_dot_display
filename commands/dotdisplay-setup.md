---
description: Install and start the claude-dot-display board daemon
---

Set up the claude-dot-display board on this machine.

1. Check whether the daemon is already installed and running:

       systemctl --user is-active dotdisplay.service

   If it is active, report that and stop — do not reinstall over a working
   service.

2. Find the panel's Bluetooth address if the user has not given one:

       dotdisplay discover

   The panel advertises as `IDM-<last six hex digits of its address>`. Do not
   use `bluetoothctl devices` for this: it only lists adapters that have
   already seen the panel, so it is empty exactly when help is needed. If
   nothing is found, ask the user rather than guessing.

3. Confirm no other process owns the radio. Only one process can hold the BLE
   link; two owners produce failures that look exactly like protocol bugs.
   The known conflict is the old sensmonlight agent:

       systemctl --user is-active sensmonlight-idotmatrix-agent.service

   If it is active, stop and tell the user before going further.

4. Run the installer from a checkout of the repository:

       DOTDISPLAY_MAC=<address> bash scripts/install.sh

5. Verify by looking, not by exit code:

       systemctl --user is-active dotdisplay.service
       journalctl --user -u dotdisplay.service -n 20 --no-pager

   Expect `panel connected` followed by `panel updated`. Then ask the user to
   confirm the panel actually changed — a clean log is not evidence that
   anything lit up. `dotdisplay check` shows a code for exactly this, but the
   daemon must be stopped first since it holds the radio.

6. Confirm the command itself is reachable, in a *fresh* shell:

       command -v dotdisplay

   The service runs the venv binary by absolute path, so the panel can light
   up perfectly while this still prints nothing. If it does print nothing,
   `~/.local/bin` is not on the user's PATH -- everything below, the status
   line and the sessions' own status reporting all depend on it, and all of
   them fail silently.

7. Mention two things the user will want to know:

   - Sessions appear by themselves once the plugin's hooks have run; the
     first row shows up after the next prompt.
   - To keep the board running when they are not logged in:

         loginctl enable-linger $USER

   - The board is also readable without the panel:

         dotdisplay board          # full view in the terminal
         dotdisplay statusline     # one line, e.g. !hwmon-d7 ?storygen *3

8. Offer the status line, but **do not edit `settings.json` yourself**. Show
   the snippet and let the user decide:

   ```json
   { "statusLine": { "type": "command", "command": "dotdisplay statusline" } }
   ```

   If they already have a status line, warn them: replacing it may break
   whatever it was doing. Several status-line scripts print nothing and exist
   purely for a side effect, so an empty-looking one is not an unused one —
   wrap it instead of replacing it.
