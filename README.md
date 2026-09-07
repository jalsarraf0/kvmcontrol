# kvmcontrol

An interactive console for the GL.iNet Comet KVM-over-IP appliance
(GL-RM1) with the GL-ATXPC add-on board — the board that wires into a
target PC's front-panel power/reset headers so the KVM can press those
buttons remotely.

## Command Center upgrade

- Dual-KVM command deck: select a target, confirm its hostname, then run
  tracked power/recovery/Wake-on-LAN. Remote control uses existing SSH trust.
- Civil-time schedules (weekdays/weekends/custom days, date exceptions, DST
  policy) plus legacy UTC daily/weekly jobs, snooze/skip, and saved presets.
- Confirmed power-transition tracking, upcoming-action warnings, cancel, and
  optional HTTPS webhook notifications (off by default; URL never exported).
- Backup with preview-before-restore. Imported jobs stay inactive.
- Synthwave / aurora / ember / ice terminal themes, compact mode, search,
  live watch, and a matching browser dashboard.

Scheduling starts **paused**. Feature tests remain deferred. See
[the operations guide](docs/COMMAND-CENTER.md) and
[hardware/software research](docs/CAPABILITIES.md).

```sh
./deploy/push.sh <first-kvm-ssh-alias> <second-kvm-ssh-alias>
# Then open https://<kvm>/command/ or use the existing SSH script path.
```

## Contents

The Python console is the primary interface. The shell entry point launches
it when installed alongside it, and retains its original shell-only mode
as a fallback (`ATX_SHELL_LEGACY=1`). Over a real TTY it uses arrow keys
(↑↓ move, ←→ jump sections, Enter select, Tab quick menu). Typed keys still
work. Without a TTY it stays line-buffered. Use `ssh -t` so the appliance
gets a pty.

- `atx-console.sh` — bash + ANSI escapes, no dependencies beyond a
  shell that supports 24-bit color.
- `atx_console.py` — Python 3 stdlib only (no pip packages). Colors
  auto-disable when output isn't a real terminal (piped, redirected to
  a file, `NO_COLOR` set, or `TERM=dumb`) instead of leaking raw escape
  codes.

Both: talk to `/usr/sbin/atxpower` over `/dev/ttyACM0` on the appliance.
Simulate the attached PC's physical power/reset buttons: status read,
graceful power on/off, hard power off, reset, plus raw short/long/reset
clicks and board serial lookup. Do **not** power off the KVM appliance
itself. Synthwave-themed with 24-bit ANSI truecolor gradient bars and
rules — needs a truecolor-capable terminal (most modern emulators; SSH
clients that clamp to 256-color will render it flatter but still
legible).

The header logo is the appliance `/etc/motd` GLKVM banner, byte-for-byte.
Quick mode (`Tab`, `j`, or `ATX_QUICK=1`) keeps status / on / off /
recovery / fleet and hides raw clicks. Arrow navigation needs a pty; if
`ssh host command` has no TTY the console prints a typed-key prompt
instead of hanging.

## Requirements

- A GL.iNet Comet (GL-RM1) with the GL-ATXPC board attached over USB
  (needs a real USB-C **data** cable — a charge-only cable will light the
  board's LED and still fail to enumerate).
- `atxpower` present at `/usr/sbin/atxpower` on the appliance (ships with
  the GL.iNet firmware for this board).
- For `atx-console.sh`: a shell that supports `bash` and 24-bit color
  escapes. For `atx_console.py`: Python 3 (3.8+; developed against 3.12).

## Copying the basic console only

For the complete upgrade, use `deploy/push.sh` as described above. The
manual copies below install only the basic entry points; automation tools
also require the `automation/` package and service.


The appliance is a minimal embedded box, not a general git host — it
doesn't pull this repo directly. Copy the script(s) over instead, keeping
a `.bak` of whatever was there before:

```sh
ssh <your-kvm-host> 'cp -p /path/to/atx-console.sh /path/to/atx-console.sh.bak'
scp -p atx-console.sh <your-kvm-host>:/path/to/atx-console.sh
ssh <your-kvm-host> 'chmod 755 /path/to/atx-console.sh'

scp -p atx_console.py <your-kvm-host>:/path/to/atx_console.py
ssh <your-kvm-host> 'chmod 755 /path/to/atx_console.py'
```

Then run either over SSH:

```sh
ssh <your-kvm-host> /path/to/atx-console.sh
ssh <your-kvm-host> python3 /path/to/atx_console.py
```

### Optional: label the attached PC

Set `ATX_TARGET_LABEL` in the shell environment the script runs under to
show a friendly label in the banner for what this particular KVM/board
controls, e.g.:

```sh
export ATX_TARGET_LABEL="rack-3 build box"
```

Left unset, the banner just omits the line — nothing site-specific is
hardcoded in the script.

## Safety

Hard power off / reset are real physical button presses on the attached
PC — unsaved work is lost exactly as it would with a real long-press. If
the attached PC happens to be the machine you're SSHed in from, a
graceful shutdown ends that session too — the script warns about this at
the power-off prompt regardless of hostname. Every state-changing action
requires a `y` confirmation; the raw click commands (short/long/reset)
skip the on/off status check entirely and go straight to the wire.

## License

The command-center addon is GPL-3.0-or-later and integrates with the
GPL-licensed GL.iNet/PiKVM KVMD libraries. See [LICENSE](LICENSE).
