# KVMCONTROL handoff — Claude Code / Grok

Updated after completing the v2 command-center work. Feature tests remain
deferred. Do not send power, HID, WOL, recovery, or webhook test traffic
as verification.

Work here: `/home/jalsarraf/git/kvmcontrol`.
Remote: https://github.com/jalsarraf0/kvmcontrol (public), branch `main`.
Never push `/home/jalsarraf/git/glkvm` (secrets in history; push disabled).

Completed in this tree:

- Scheduler v2: civil-time jobs, presets, settings, tracked run/cancel,
  backup/restore preview, notification worker that cannot kill itself on
  storage errors, in-flight ATX tracking, v1→v2 migration, v2→v1 rollback
  conversion.
- Fleet profiles over existing SSH (`StrictHostKeyChecking=yes`).
- ConsoleTools handlers and selected-target routing. Notes/diagnostics/
  appearance stay local. Remote 1/2/3 use tracked scheduler; raw/hard stay
  local-only.
- Browser command center: fleet cards, calendar form, presets, tracked
  power/recovery, snooze/skip, settings, backup restore, reduced-motion.
- Installer snapshots scheduler state, waits for a real stop, removes
  stale sockets, optional private `local/fleet.json` per host.

Private per-device fleet files stay under ignored `local/`. Example:
`deploy/fleet.example.json`.
