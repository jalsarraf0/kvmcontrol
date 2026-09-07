# Command Center operations guide

## Access

The upgraded Python console keeps the original numbered ATX controls and
adds an operations section. `atx-console.sh` launches the Python interface
when the adjacent Python file exists; `ATX_SHELL_LEGACY=1` selects the old
shell-only implementation. The header is the exact Comet `/etc/motd`
GLKVM logo. On a TTY, move with arrow keys, jump sections with ←→, select
with Enter, toggle quick mode with Tab or `j`, and confirm with ←→ / y / n
(default NO). Typed keys still work. `ssh -t user@kvm python3 /usr/share/kvmcontrol/atx_console.py` if your client does not allocate a pty. `ATX_LINE_MENU=1` forces the typed prompt.

| Key | Operation |
| --- | --- |
| f | Switch fleet target (local or SSH peer) |
| r | Guided recovery: off → confirmed off → on |
| v | Watch workflow phase and countdown |
| c | Cancel remaining workflow steps |
| a | Add on/off/wol/recover schedule (calendar or UTC interval) |
| l | Pause, resume, snooze, skip, or delete a job |
| m | Save, apply, or delete maintenance presets |
| p | Pause all scheduling and request cancel |
| e | Review the queue and enable scheduling |
| h | Show the latest 30 schedule events |
| w | Tracked Wake-on-LAN through the scheduler |
| d | Passive local inventory plus selected-target scheduler status |
| n | Append to the notebook on **this** KVM |
| s | Time zone, warning, transition timeout, optional webhook |
| b / i | Backup and preview-before-restore |
| t / / | Appearance (theme/compact/animation) and command search |
| x | Secret-free backup export to the KVM user directory |

Times accept `+15m`, `+2h`, `+1d`, or ISO timestamps with explicit offsets,
for example `2026-09-08T08:00:00-05:00`. `daily`/`weekly` remain fixed UTC
intervals. `weekdays`/`weekends`/`calendar` use civil local time: missing
spring-forward hours are skipped; repeated fall-back hours use the first
occurrence. Jobs may be at most 366 days ahead. Numbered raw/hard ATX
clicks stay on the SSH session’s local board.

The browser dashboard is at `https://<kvm>/command/`. Sign in through the
existing GL.iNet login. A Command Center link is added to the vendor page.
The dashboard adds live status/countdowns, persistent schedule controls,
confirmed keyboard shortcuts (including BIOS keys), up to 20 custom
shortcuts, local notes, an action launcher (`/` outside an input), diagnostic
exports and schedule-history export. Open the original console for video,
virtual media, recording and full keyboard/mouse control.

Browser notes/shortcuts stay in that browser. Terminal notes are separate
and saved on the KVM. Neither notebook should contain passwords. Display
refresh reads status every 10 seconds while visible; no discovery scan,
wake, power command or keyboard injection runs automatically.

## Service and persistence

The addon runs independently of the vendor KVMD daemon and uses the
KVMD/aiohttp libraries already installed in GL.iNet firmware. Its HTTP
listener is a root-only Unix socket under `/run/kvmcontrol`. Nginx uses the
existing `/auth_check` authentication before forwarding scheduler traffic.
No new TCP listener, container, password, remote account, or tunnel is added.

Schedules start **paused**. Enabling persists across service restarts.
A fresh deployment creates no jobs and sends no commands. The durable store
is `/etc/kvmd/user/power-schedules.json`; at most 64 jobs and 100 events are
kept. Writes use a 0600 file, atomic replacement, and fsync. Invalid or
unwritable storage locks execution instead of silently discarding data.
A process lock prevents competing scheduler daemons.

A job occurrence is saved as consumed before any hardware request. If
execution is interrupted, the service never retries that occurrence. Jobs
due before startup, while paused, or more than 60 seconds late are skipped.
This favors at-most-once attempts over guaranteed execution. Pausing cannot
undo an already sent command. Repeating jobs advance to the next future
interval instead of replaying a backlog. Failed commands are recorded
without automatic retries or forced-shutdown escalation.

Manual power on/off/recovery from the dashboard uses `/api/scheduler/run`
and requires `confirm_target` to equal the appliance hostname. Two
consecutive ATX readings must match before a transition is reported
confirmed. Optional HTTPS webhooks are off by default; saving settings
never sends a test; ordinary status and backups redact the URL.

Fleet profiles are optional (`/etc/kvmd/user/kvmcontrol-fleet.json`).
Missing file means local-only. Installer copies `local/fleet.json` from the
staging tree only when no profile already exists. SSH uses BatchMode and
StrictHostKeyChecking. Keyboard/HID shortcuts are never proxied to a
remote target.

Graceful shutdown is a request to the OS, not proof of a shutdown. A sent
WOL packet does not prove startup. ATX actions require a recognized power
state and connected accessory. Sleep/unknown states are not inferred to
be off. Keep the appliance powered, its clock synchronized, and the LAN
available. The independent addon does not share a lock with vendor/manual
ATX controls; avoid simultaneous physical operations from multiple clients.

## Install

From a clean checkout on your workstation:

```sh
./deploy/push.sh <first-kvm-ssh-alias> <second-kvm-ssh-alias>
```

The installer targets GL.iNet's Python 3.12 / Buildroot firmware layout.
It uploads an explicit file set over tar/SSH (compatible with Dropbear),
parses Python source, validates nginx syntax, makes a timestamped backup,
installs the addon and starts its service. It reloads nginx; it does not
restart KVMD or reboot either the KVM or its attached PC. Existing scripts
at `/home/*/scripts/atx-console.sh` and `atx_console.py` become launchers.
No feature tests are part of installation.

Canonical script paths after installation:

```sh
python3 /usr/share/kvmcontrol/atx_console.py
bash /usr/share/kvmcontrol/atx-console.sh
/etc/init.d/S99kvmcontrol status
```

Backups and exact installed SHA-256 manifests are under
`/etc/kvmd/user/kvmcontrol-backups/<timestamp>/`. The installer rolls back
changed files if configuration validation or service startup fails.
For a later rollback, first pause scheduling, then run:

```sh
python3 /etc/kvmd/user/kvmcontrol-backups/<timestamp>/rollback.py
```

Rollback restores the prior files. The installer also snapshots
`power-schedules.json`. Rolling a v2 store back onto v1 code converts jobs
to the v1 schema, pauses scheduling, and leaves a `.v2` copy beside the
live file. New civil-time jobs become one-shot at their next `at`.
Firmware upgrades may remove addon files; reinstall from this repository
after checking compatibility. Do not replace the commercial firmware's
compiled KVMD modules with source from a different release.

## API

All browser endpoints use the existing KVM authentication. Responses use
`{"ok":true,"result":...}`.

| Method | Endpoint | Body |
| --- | --- | --- |
| GET | `/api/scheduler` | none (webhook URL redacted) |
| GET | `/api/scheduler/status` | none |
| GET | `/api/scheduler/fleet` | none |
| POST | `/api/scheduler/fleet` | `{"target":"peer","path":"/scheduler/run","body":{…}}` |
| POST | `/api/scheduler/armed` | `{"armed":false}` |
| POST | `/api/scheduler/jobs` | name, action, at, repeat; optional timezone, wall_time, days, exceptions, mac |
| POST | `/api/scheduler/job` | `{"id":"…","operation":"pause"}`; also resume/delete/snooze/skip |
| POST | `/api/scheduler/run` | `{"action":"on","confirm_target":"<hostname>"}` |
| POST | `/api/scheduler/cancel` | none |
| POST | `/api/scheduler/presets` | job fields, or `{"operation":"delete","name":"…"}` |
| POST | `/api/scheduler/settings` | timezone, warning_seconds, transition_timeout, notifications |
| GET | `/api/scheduler/backup` | none |
| POST | `/api/scheduler/restore` | preview or token-confirmed commit; merge or replace |

Actions: `on`, `off`, `wol` (requires `mac`), `recover`. Repeats: `once`,
`daily`, `weekly`, `calendar`, `weekdays`, `weekends`. Restore imports jobs
inactive and never restores webhook URLs or history.

## Acceptance later

Per the user's instruction, feature testing was deferred. Only source
syntax, deployment configuration, transferred files and service process
readiness were checked. Before relying on unattended scheduling, manually
check auth and UI, correctly wired ATX state, on/off behavior, WOL/NIC
support, browser/terminal queue consistency, pause/cancel, restart and
missed-run behavior, bad storage handling, and recurrence around a clock
change. Keyboard shortcuts and notebooks also remain for user acceptance.
