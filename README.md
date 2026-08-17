# kvmcontrol

Scripts for the GL.iNet Comet KVM-over-IP appliances (GL-RM1 + GL-ATXPC
add-on board) that sit in front of `amarillo` and `dominus-nobara`.

## Hosts

- `amarillokvm` (root@, Tailscale `100.114.64.103`) — attached to `amarillo`
- `dominus-kvm` (root@, Tailscale `100.66.206.102`) — attached to
  `dominus-nobara`

Both run identical copies of the scripts here under
`/home/jalsarraf/scripts/` on the appliance itself (root's home on these
boxes is `/home/jalsarraf`, matching the appliance's default user layout —
not to be confused with the `jalsarraf` user on the PCs they control).

## Contents

- `atx-console.sh` — interactive menu-driven console for
  `/usr/sbin/atxpower` (talks to the GL-ATXPC board over
  `/dev/ttyACM0`). Simulates the attached PC's physical power/reset
  buttons: status read, graceful power on/off, hard power off, reset, plus
  raw short/long/reset clicks and board serial lookup. Does not power off
  the KVM appliance itself. Synthwave-themed (24-bit ANSI truecolor
  gradient bars/rules); requires a truecolor-capable terminal.

## Deploying

This repo is not pulled by the appliances — they're minimal embedded
boxes, not general git hosts. Deploy by copying the script over and
keeping a `.bak` of whatever was there:

```sh
for h in amarillokvm dominus-kvm; do
  ssh "$h" 'cp -p /home/jalsarraf/scripts/atx-console.sh /home/jalsarraf/scripts/atx-console.sh.bak'
  scp -p atx-console.sh "$h:/home/jalsarraf/scripts/atx-console.sh"
  ssh "$h" 'chmod 755 /home/jalsarraf/scripts/atx-console.sh'
done
```

## Safety

Hard power off / reset are real physical button presses on the attached
PC — unsaved work is lost exactly as it would be with a real long-press.
`dominus-kvm` controls the same machine these scripts might be run from
(`dominus-nobara`). The script confirms before every state-changing
action; raw click commands skip on/off logic and go straight to the wire.
