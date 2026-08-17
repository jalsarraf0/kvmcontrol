# kvmcontrol

An interactive console for the GL.iNet Comet KVM-over-IP appliance
(GL-RM1) with the GL-ATXPC add-on board — the board that wires into a
target PC's front-panel power/reset headers so the KVM can press those
buttons remotely.

## Contents

- `atx-console.sh` — menu-driven console for `/usr/sbin/atxpower` (talks
  to the GL-ATXPC board over `/dev/ttyACM0` on the appliance). Simulates
  the attached PC's physical power/reset buttons: status read, graceful
  power on/off, hard power off, reset, plus raw short/long/reset clicks
  and board serial lookup. Does **not** power off the KVM appliance
  itself. Synthwave-themed with 24-bit ANSI truecolor gradient bars and
  rules — needs a truecolor-capable terminal (most modern emulators; SSH
  clients that clamp to 256-color will render it flatter but still
  legible).

## Requirements

- A GL.iNet Comet (GL-RM1) with the GL-ATXPC board attached over USB
  (needs a real USB-C **data** cable — a charge-only cable will light the
  board's LED and still fail to enumerate).
- `atxpower` present at `/usr/sbin/atxpower` on the appliance (ships with
  the GL.iNet firmware for this board).
- A shell on the appliance that supports `bash` and 24-bit color escapes.

## Deploying

The appliance is a minimal embedded box, not a general git host — it
doesn't pull this repo directly. Copy the script over instead, keeping a
`.bak` of whatever was there before:

```sh
ssh <your-kvm-host> 'cp -p /path/to/atx-console.sh /path/to/atx-console.sh.bak'
scp -p atx-console.sh <your-kvm-host>:/path/to/atx-console.sh
ssh <your-kvm-host> 'chmod 755 /path/to/atx-console.sh'
```

Then run it over SSH: `ssh <your-kvm-host> /path/to/atx-console.sh`.

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
