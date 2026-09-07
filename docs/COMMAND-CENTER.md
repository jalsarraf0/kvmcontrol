# Command Center operations guide

## Access

The upgraded Python console keeps the original numbered ATX controls and
adds an operations section. `atx-console.sh` launches the Python interface
when the adjacent Python file exists; `ATX_SHELL_LEGACY=1` selects the old
shell-only implementation. No raw terminal mode or arrow keys are needed.

| Key | New operation |
| --- | --- |
| a | Add a power-on, graceful shutdown, or Wake-on-LAN schedule |
| l | List schedules; pause, resume, or delete a selected job |
| p | Pause all scheduling |
| e | Review the queue and enable scheduling |
| h | Show the latest 30 schedule events |
| w | Send a confirmed Wake-on-LAN packet |
| d | Passive device, clock, uptime, storage and service diagnostics |
| n | Append to the KVM notebook |
| x | Export schedules and history to the KVM user directory |

Times accept `+15m`, `+2h`, `+1d`, or ISO timestamps with explicit offsets,
for example `2026-09-08T08:00:00-05:00`. Daily means every 24 hours;
weekly means every 7 days. Repeats remain anchored in UTC, so local time
shifts when daylight saving changes. Jobs may be at most 366 days ahead.

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

Rollback restores the prior files and retains schedule data for diagnosis.
Firmware upgrades may remove addon files; reinstall from this repository
after checking compatibility. Do not replace the commercial firmware's
compiled KVMD modules with source from a different release.

## API

All browser endpoints use the existing KVM authentication. Responses use
`{"ok":true,"result":...}`.

| Method | Endpoint | Body |
| --- | --- | --- |
| GET | `/api/scheduler` | none |
| POST | `/api/scheduler/armed` | `{"armed":false}` |
| POST | `/api/scheduler/jobs` | `{"name":"Morning","action":"on","at":"2026-09-08T08:00:00-05:00","repeat":"daily"}` |
| POST | `/api/scheduler/job` | `{"id":"…","operation":"pause"}`; also resume/delete |

Actions: `on`, `off`, `wol` (requires `mac`). Repeats: `once`, `daily`,
`weekly`. Export is for backup/review; no automatic rearming import exists.

## Acceptance later

Per the user's instruction, feature testing was deferred. Only source
syntax, deployment configuration, transferred files and service process
readiness were checked. Before relying on unattended scheduling, manually
check auth and UI, correctly wired ATX state, on/off behavior, WOL/NIC
support, browser/terminal queue consistency, pause/cancel, restart and
missed-run behavior, bad storage handling, and recurrence around a clock
change. Keyboard shortcuts and notebooks also remain for user acceptance.
