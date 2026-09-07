# Comet capability research and implementation scope

Research date: 2026-09-07. This is an inventory of the GL-RM1 family and
the inspected GLKVM firmware source, not a claim that every feature is enabled on an installed
firmware or that an accessory is physically present.

## Hardware boundary

The GL-RM1 product specifications list a quad-core Cortex-A7, 1 GB RAM,
8 GB eMMC, gigabit Ethernet, HDMI input, USB 2.0, and 5 V / 2 A USB-C
power. Advertised capture reaches 4K at 30 fps. Those limits favor a small
static interface and a lightweight scheduler rather than a new database,
container platform, or video processing pipeline. Source:
[GL.iNet specifications](https://www.gl-inet.com/products/gl-rm1/),
[dated manufacturer datasheet](https://static.gl-inet.com/www/images/products/datasheet/rm1_datasheet_20250320.pdf).
These are manufacturer claims, not independently measured performance.

The vendor documents the USB-C control connection separately from the
USB-A accessory port. Keyboard and mouse emulation are available before
an OS boots; a working HDMI path and appropriate USB connection are still
required. Source: [RM1 overview](https://docs.gl-inet.com/kvm/en/user_guide/gl-rm1/product_overview/).

An ATX board presses the computer's front-panel buttons. It does not
switch the mains supply, control a PSU independently, or shut down the
KVM itself. GL-ATXPC reports power status, but has no HDD activity LED.
One RM1 controls one ATX board. Source:
[GL-ATXPC guide](https://docs.gl-inet.com/kvm/en/user_guide/gl-atx-board/).
The corresponding local implementation is `kvmd/plugins/atx/glatx.py`,
which invokes `/usr/sbin/atxpower /dev/ttyACM0 <action>`.

## Software capability matrix

| Capability | Evidence in the inspected GLKVM firmware source | Boundary / implementation decision |
| --- | --- | --- |
| ATX on / graceful off / hard off / reset | `api/atx.py`, `plugins/atx/glatx.py` | Scheduler permits only on and graceful off; no unattended hard reset or long press. |
| Wake-on-LAN | `api/wol.py`, `plugins/ugpio/wol.py` | Wakes compatible NIC/firmware; cannot power off. Scheduler sends a UDP LAN broadcast, without shell execution. |
| USB keyboard, mouse, shortcuts and paste | `api/hid.py`, `plugins/hid/` | New keyboard deck uses the existing shortcut endpoint; no automatic keystrokes. |
| Virtual media and file transfer | `api/msd.py`, `plugins/msd/otg/` | Full console retains image and USB storage workflows; media capacity is limited by available storage. |
| Video and audio paths | `streamer.py`, `web/share/js/kvm/stream_*` | Existing stream is retained; no new codec or latency claims. |
| Recording, macros, OCR | `api/recorder.py`, `ocr.py`, existing console modules | Availability depends on installed binaries, firmware and configuration. |
| Remote networking | Tailscale, ZeroTier, NetBird, Cloudflare APIs | Existing connectivity is retained; no tunnel, firewall or routing changes. |
| Redfish / IPMI / VNC | `api/redfish.py`, `apps/ipmi`, `apps/vnc` | Present in source; may need separate configuration and services. |
| Persistent power schedules | New `automation/scheduler.py` | Opt-in, bounded JSON store; UTC intervals and civil-time weekdays/weekends/calendar with DST policy. |
| Notes, action launcher, diagnostics export | New `web/command` frontend | Browser-only notes; no extra server database or dependencies. |

The [manufacturer console guide](https://docs.gl-inet.com/kvm/en/user_guide/gl-rm1/console_guide/)
also documents virtual media, WOL, keyboard shortcuts, overlays, recording
and display settings. It changes with firmware, so source support and
installed behavior must be distinguished. Upstream
[PiKVM HTTP API documentation](https://docs.pikvm.org/api/) corroborates the
API architecture and host-power/HID/media concepts, but is not a GL.iNet
firmware compatibility guarantee. The local API signatures take precedence.

## Scheduling semantics

Schedules live in `/etc/kvmd/user/power-schedules.json` on the appliance,
with mode 0600 for the atomically replaced file. This is native appliance
storage, not Docker storage. No Docker components are introduced.

All scheduling starts paused. Arming persists across daemon restarts.
Jobs are validated, capped at 64, and serialized against mutations.
The last 100 creation, change, skip and dispatch events are retained.
Writes flush and fsync before replacing the store and syncing its directory.
A storage failure locks dispatch until service restart; a malformed store is not
overwritten. One automation service process owns this file, enforced with a process lock.

A due occurrence is consumed and recorded before sending anything to the
hardware. A crash after that point leaves an uncertain dispatch in history
and never causes a retry. Runs due before daemon startup or while paused
are skipped; active worker delays over 60 seconds are also skipped.
Repeating jobs move to the next future interval rather than catching up.
This favors at-most-once attempts over guaranteed execution.

The browser sends offset-aware ISO timestamps, stored as UTC. Legacy
`daily`/`weekly` jobs remain fixed elapsed intervals. `weekdays` /
`weekends` / `calendar` jobs use IANA civil time: nonexistent
spring-forward times are skipped; repeated fall-back times use the first
occurrence. Keep both clocks synchronized. The UI shows clock skew; it does
not change the appliance clock. Pause cannot retract an already dispatched
physical command. An accepted graceful shutdown does not prove that the OS
shut down, and a sent WOL packet does not prove boot. Tracked run/recovery
requires two consecutive matching ATX readings before reporting success.

## Coverage and remaining uncertainty

Research used native web search, fetched manufacturer documentation,
upstream PiKVM API documentation, and inspection of the local source. No
Brave, SearXNG, Context7 or git-mcp research backends were connected in this
session. Social/X research is not relevant to hardware/API integration.
Several manufacturer pages are one publisher and are not independent
corroboration; those hardware statements are explicitly attributed above.

No feature tests, host-power operations, HID injection, video capture,
network scans or WOL transmissions were performed during this change.
The user's later acceptance checks should cover clock behavior, wired ATX
state, browser auth, missed-run handling, storage failure, power and WOL,
and the UI on the actual firmware. New software is not a firmware upgrade.

## Repository integration

The capability source paths above refer to the inspected
[GL.iNet KVMD repository](https://github.com/gl-inet/glkvm), not files shipped
in this smaller addon repository. Actual devices were inventoried read-only:
they ship Python 3.12 bytecode and a separate vendor frontend. Consequently
this addon registers the scheduler on its own Unix-socket service and uses
the installed GL-ATX plugin, rather than replacing the vendor daemon.
