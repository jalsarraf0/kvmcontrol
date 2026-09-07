# KVMCONTROL handoff — Claude Code / Grok

Updated 2026-09-07. The user interrupted implementation to request handoff
notes, then explicitly requested a LOCAL commit. This checkpoint is work
in progress. Do not confuse it with the deployed release.

## Repository and deployed baseline

Work here: `/home/jalsarraf/git/kvmcontrol`.
Remote: https://github.com/jalsarraf0/kvmcontrol (public), branch `main`.
The deployed/pushed baseline is `60f4c31` — power automation, command dashboard,
and backed-up Comet deployment. This new checkpoint has NOT been pushed or
deployed. The existing devices are still on the baseline.

The original session opened in `/home/jalsarraf/git/glkvm`, a different,
PRIVATE firmware checkout. That checkout has an earlier local-only commit
`4ce8951`. Its history contains secrets and its origin push URL/hook are
disabled. Never push that checkout or copy its `local/` notes into the public
script repository. The intended script project is kvmcontrol.

## User intent and constraints

The user requested ALL these additions, with an awesome polished TUI and
animations:

1. Dual-KVM command center with clear target identity before commands.
2. Weekdays/weekends/custom weekdays, date exceptions, DST-aware local times.
3. Saved maintenance presets.
4. Confirmed power-transition tracking with timeout/uncertainty reporting.
5. Upcoming-action warnings, quick cancel and snooze.
6. Guided shutdown → confirmed off → start recovery, with cancellation.
7. Optional completion/failure notifications.
8. Compact mode, search, themes, persistent target/status header, animations.
9. Backup and preview-before-restore of schedules/settings.

Earlier the user authorized committing, pushing and deploying completed
updates to BOTH KVMs. However the latest request is a LOCAL checkpoint and
handoff; do not publish/deploy this incomplete checkpoint. Resume completion
only when asked. Feature tests remain explicitly deferred by the user.
Do not send power, HID, WOL or notification commands as verification. Do not
run the test suite. Syntax parsing and source/configuration review were the
only checks used for the prior release. No functional validation has been
performed for this WIP. Do not enable schedules or configure a real webhook
on the user's behalf without their chosen destination/settings.

Docker, if ever involved, must use mounted `/mnt/nvmer0` only; existing
Docker data-root `/mnt/nvmer0/docker-data` must remain unchanged. No Docker is
needed here. Do not touch dominus-ai-stack / port 1234.

## Baseline architecture

- `atx_console.py`: line-buffered synthwave console; no raw-terminal mode.
- `atx-console.sh`: launches adjacent Python console; legacy shell fallback.
- `automation/client.py`: standard-library HTTP over root-only Unix socket.
- `automation/scheduler.py`: durable bounded JSON schedules; KVMD/aiohttp
  imports supplied by installed GL.iNet firmware, not a standalone pip app.
- `automation/service.py`: independent addon, leaves vendor KVMD running.
- Socket `/run/kvmcontrol/automation.sock`, service `/etc/init.d/S99kvmcontrol`.
- State `/etc/kvmd/user/power-schedules.json` (0600 atomic replacement/fsync).
- Installed source `/usr/share/kvmcontrol/`; dashboard `/command/`.
- nginx authenticates scheduler traffic using existing `/auth_check`.
- `deploy/push.sh`, `install.py`, `rollback.py`: explicit files, tar over SSH,
  timestamped backups, nginx reload, no KVMD restart or host reboot.
- `docs/COMMAND-CENTER.md` and `docs/CAPABILITIES.md` describe the BASELINE;
  they have not yet been updated for this WIP.

## WIP code already written (not ready to run/deploy)

### automation/calendar_rules.py — new

Civil-time recurrence using stdlib ZoneInfo. Calendar/weekdays/weekends,
custom weekday list, up to 64 excluded dates. DST policy: skip nonexistent
spring-forward times, choose first occurrence of repeated fall-back time.
Legacy daily/weekly remain fixed UTC intervals. Adds wall_time, timezone,
days, exceptions and anchor. Review type validation, first-run normalization,
snooze/skip recurrence anchors and bounds; none has been feature-tested.

### automation/scheduler.py — substantially expanded

Version-2 state with proposed v1 migration, presets and settings. New API:

- GET `/scheduler/status`: hostname, ATX power, active workflow, queue summary.
- POST `/scheduler/run`: on/off/wol/recover, requires `confirm_target` hostname.
- POST `/scheduler/cancel`: cancel remaining workflow steps.
- POST `/scheduler/settings`: timezone, warning_seconds, transition_timeout,
  optional HTTPS JSON webhook (default off; URL redacted from regular state).
- POST `/scheduler/presets`: save/replace by name, or delete; maximum 16.
- GET `/scheduler/backup`: v2 backup with webhook URL excluded.
- POST `/scheduler/restore`: preview / token-confirmed commit, merge/replace,
  requires paused scheduler/no active execution; imports jobs inactive.
- Existing job endpoint now supports snooze and skip.

Dispatcher moved out of the queue lock into a separate task so cancellation
and status remain available. Two consecutive observed power states are
required. Recovery waits for off, settles, rechecks, then sends on. No forced
shutdown or retry escalation. Scheduled occurrence is durably consumed before
hardware dispatch. Notification task uses an in-memory bounded queue and one
HTTPS attempt, no redirects/retries, no automatic test send.

REVIEW REQUIRED: migration and field validation; lock/cancellation races;
shutdown handling; command tasks that outlive timeout because the vendor ATX
methods use shielding; cleanup of __active if save fails or task cancels;
retrieval/logging of background task exceptions; ensuring notification-worker
storage failures cannot silently kill its task; imported preset limits and
names; secret-free error/event payloads. `__active` is volatile; persisted
history remains the interrupted-attempt record. Some messages/docstrings
still incorrectly say restart KVMD rather than the addon service.

### automation/fleet.py / fleet_api.py — new

Profiles read from `/etc/kvmd/user/kvmcontrol-fleet.json`, 1–8 validated targets
with id, label, host (empty for local), url. Missing file defaults local-only.
No actual fleet profiles have been created yet. Remote request uses existing
SSH trust (`-T -o BatchMode=yes -o StrictHostKeyChecking=yes`), fixed Python
RPC command and JSON stdin. API paths allowlisted. Fleet overview performs
concurrent read-only status requests; errors are per-target.

GET/POST `/scheduler/fleet` expose overview/proxy. Review bounds/timeouts and
exception handling. Intended: one dashboard can manage both devices over
existing mesh SSH, no credentials in browser/config exports. Do not bypass
host-key verification to make it work.

### automation/client.py / service.py / deploy/nginx-command.conf

Client gained a fixed RPC entry point for SSH. Service registers fleet API,
notification task, execution cleanup. Request body limits increased to 128 KiB
for backup restore, nginx timeout to 50s. Client timeout remains 35s and fleet
subprocess timeout 40s; review alignment with browser timeout (still 30s).

### automation/terminal_design.py — new

Line-oriented header/status boxes, responsive widths, compact display,
synthwave/aurora/ember/ice palettes, entrance sweep (~0.42s) with cursor cleanup.
Settings path `/etc/kvmd/user/kvmcontrol-ui.json`, configurable via ATX_UI_CONFIG.
No raw input mode. Honor NO_COLOR / dumb terminal / ATX_NO_ANIMATION.

### atx_console.py — partially wired, CURRENTLY INCOMPLETE

Adds theme/compact settings, target identity, `console.request()` for
local/fleet routing, new header/menu and EXTRA_ITEMS. New controls:
f fleet, m presets, r recover, c cancel, v watch, b backup, i restore,
s settings, t appearance, / search. Remote numbered actions 1/2/3 delegate
to remote_status/on/off; raw/hard controls are intentionally local-only.

CRITICAL: `automation/console_tools.py` HAS NOT BEEN UPDATED. Most new menu
handlers DO NOT EXIST, including all three remote handlers. Selecting them
will fail. Existing tools still call imported local `request()` directly,
not `self.console.request()`. Finish this BEFORE any remote target selection
is enabled, or operations may accidentally target the local device.

New header reads scheduler state on each redraw (no power command). Themes
only partly drive old gradient roles. Search, appearance editor, live watch,
actual fleet selection, presets UI, restore preview, calendar prompts,
snooze/skip controls and notification configuration remain to implement.
Keep frame widths/ANSI display widths and narrow-terminal behavior coherent.

## Remaining work in order

1. Review all WIP source/diff; fix structural mistakes before proceeding.
2. Finish ConsoleTools handlers and route every target-specific action through
   the selected target. Keep local notes/diagnostics clearly local or extend
   them deliberately; never silently operate on a different machine.
3. Finish TUI themes/compact/search/watch/countdowns/animation controls.
4. Update browser HTML/JS/CSS: no v2 browser edits have been made yet. Add fleet
   cards/selection, clear confirmation target, calendar/DST form, exceptions,
   presets, tracked run/cancel, snooze/skip, optional alerts, backup restore
   preview, and animations honoring prefers-reduced-motion. Existing browser
   ATX buttons still call vendor `/api/atx/power`, bypassing new tracking.
   Existing non-scheduler HID/WOL actions must NOT hit local hardware while a
   remote target is selected; proxy them explicitly or direct users to the
   remote console and disable unsupported remote controls.
5. Configure private per-device fleet profiles at install time or explicit
   local setup; no hardcoded site addresses/keys in the public source.
6. Harden deployment upgrades: check service has really stopped before file
   replacement, remove/validate stale sockets, ensure startup readiness checks
   refer to the NEW process/socket, preserve paused state, and handle rollback
   from v2 to v1 without leaving an unreadable state file. Current installer
   backs up installed code but NOT scheduler state; migration makes this a
   real rollback concern. Do not erase user jobs. Existing init stop timeout
   and install process checks deserve source review.
7. Update docs accurately. Feature tests stay deferred. Do syntax parsing,
   shell/JS/config checks only unless the user changes that instruction.
8. When complete and authorized to resume delivery, commit/push kvmcontrol
   and deploy sequentially to both KVMs with backups. Verify checksums/service
   state only; no sample jobs, WOL, HID, recovery, notification or power tests.

## Documentation checked during implementation

- https://docs.python.org/3/library/zoneinfo.html — IANA data and fold behavior.
- https://docs.aiohttp.org/en/stable/client_quickstart.html — async HTTP client.
- Prior hardware/software research is in docs/CAPABILITIES.md.

## Agent collaboration

Claude Code and Grok should use this same handoff. If working concurrently,
explicitly split files/ownership; all agents share the working tree. Suggested
split (not a claim that agents were started): Claude owns ConsoleTools/TUI
and browser integration; Grok reviews scheduler/calendar/fleet/lifecycle and
deployment. Coordinate endpoint contracts before edits. No subagents or
background feature tests were started in this checkpoint session.

Private deployment details are in ignored `local/HANDOFF-DEPLOYMENT.md`.
