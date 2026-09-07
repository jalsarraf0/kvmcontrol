# SPDX-License-Identifier: GPL-3.0-or-later
"""Line-oriented tools; importing this module performs no hardware I/O."""
import datetime
import json
import os
from pathlib import Path
import re
import socket
import time

from automation.client import request


def plain(value):
    """Never replay terminal control characters from saved names or logs."""
    return "".join(char for char in str(value) if char.isprintable())


def display_time(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M %Z")


class ConsoleTools:
    def __init__(self, console):
        self.console = console
        self.ui = console.ui

    def run(self, action):
        try:
            getattr(self, action)()
        except (OSError, ValueError, RuntimeError) as error:
            self.ui.error(plain(error))
            self.ui.warn("Automation needs the kvmcontrol service installed and running.")

    def overview(self):
        state = request()
        self.ui.line("  AUTOMATION  /  " + ("LOCKED" if state["error"] else "ARMED" if state["armed"] else "PAUSED"))
        if state["error"]:
            self.ui.error(state["error"])
        jobs = sorted(state["jobs"], key=lambda job: job["at"])
        if not jobs:
            self.ui.line("  No schedules. Choose [a] to create one.")
        for number, job in enumerate(jobs, 1):
            status = "QUEUED" if job["enabled"] else "INACTIVE"
            self.ui.line(f"  {number:>2}. [{status}] {plain(job['name'])} / {job['action']}")
            self.ui.line(f"      {display_time(job['at'])} · {job['repeat']} · {job['id'][:8]}")
        return jobs

    def queue(self):
        jobs = self.overview()
        if not jobs:
            return
        answer = self.ui.ask("  Job number to manage (Enter to return): ")
        if not answer:
            return
        index = int(answer) - 1
        if index < 0 or index >= len(jobs):
            raise ValueError("Choose a displayed job number")
        job = jobs[index]
        operation = self.ui.ask("  pause / resume / delete (Enter to cancel): ")
        if operation not in ("pause", "resume", "delete"):
            return
        if self.console.confirm(f"{operation.title()} schedule '{plain(job['name'])}'?"):
            request("/scheduler/job", {"id": job["id"], "operation": operation})
            self.ui.ok("Schedule updated")

    def add(self):
        state = request()
        name = self.ui.ask("  Schedule name (Enter to cancel): ")
        if not name:
            return
        action = self.ui.ask("  Action: on / off / wol: ")
        if action not in ("on", "off", "wol"):
            raise ValueError("Choose on, off, or wol")
        mac = self.ui.ask("  Target MAC (AA:BB:CC:DD:EE:FF): ") if action == "wol" else ""
        self.ui.line("  Time: +15m, +2h, +1d, or ISO date/time with offset")
        self.ui.line("  Example: 2026-09-08T08:00:00-05:00")
        answer = self.ui.ask("  First execution: ")
        if not answer:
            return
        match = re.fullmatch(r"\+(\d{1,6})([mhd])", answer.strip())
        if match:
            seconds = int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]]
            due = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        else:
            due = datetime.datetime.fromisoformat(answer.replace("Z", "+00:00"))
            if due.tzinfo is None:
                raise ValueError("Include an explicit UTC offset, for example -05:00")
        repeat = self.ui.ask("  Repeat: once / daily (24h) / weekly (7d) [once]: ")
        if repeat is None:
            return
        body = {"name": name, "action": action, "at": due.isoformat(), "repeat": repeat or "once", "mac": mac or ""}
        self.ui.line(f"  {action.upper()} · {display_time(body['at'])} · {body['repeat']}")
        self.ui.warn("Repeats use fixed UTC intervals; local time shifts with daylight saving.")
        if state["armed"]:
            self.ui.warn("Scheduling is armed: this job will run without an open terminal.")
        if self.console.confirm("Save this power schedule?"):
            request("/scheduler/jobs", body)
            self.ui.ok("Saved on this KVM" + ("" if state["armed"] else "; scheduler remains paused"))

    def pause(self):
        request("/scheduler/armed", {"armed": False})
        self.ui.ok("All scheduling paused. Already dispatched commands may still finish.")

    def enable(self):
        self.overview()
        self.ui.warn("These jobs run even after you disconnect. Check clock and target first.")
        if self.console.confirm("Enable scheduled power operations?"):
            request("/scheduler/armed", {"armed": True})
            self.ui.ok("Scheduling enabled")

    def history(self):
        state = request()
        if not state["history"]:
            self.ui.line("  No scheduled activity yet.")
        for event in state["history"][:30]:
            self.ui.line(f"  {display_time(event['time'])} [{plain(event['status']).upper()}] {plain(event['name'])}")
            self.ui.line("    " + plain(event["detail"]))

    def diagnostics(self):
        settings = self.console.settings
        self.ui.line("  SYSTEM / PASSIVE INVENTORY")
        self.ui.line(f"  KVM: {plain(settings.host)}")
        self.ui.line(f"  Clock: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        self.ui.line(f"  ATX tool: {'present' if self.console.tool_available() else 'missing'}")
        self.ui.line(f"  ATX device node: {'present' if self.console.board_present() else 'missing'}")
        try:
            seconds = float(Path('/proc/uptime').read_text().split()[0])
            self.ui.line(f"  Uptime: {int(seconds // 86400)}d {int(seconds % 86400 // 3600)}h")
        except (OSError, ValueError):
            pass
        usage = os.statvfs('/etc/kvmd/user')
        self.ui.line(f"  User storage free: {usage.f_bavail * usage.f_frsize // 1048576} MiB")
        try:
            state = request()
            self.ui.line(f"  Automation: {'LOCKED' if state['error'] else 'armed' if state['armed'] else 'paused'}")
            self.ui.line(f"  Saved schedules: {len(state['jobs'])}")
        except OSError:
            self.ui.warn("Automation service unavailable")
        self.ui.line("  Web dashboard: https://<this-kvm>/command/")
        self.ui.line("  No power, USB, video, or network operation was performed.")

    def notes(self):
        path = Path('/etc/kvmd/user/kvmcontrol-notes.txt')
        self.ui.line("  NOTEBOOK / stored on this KVM, separate from browser notes")
        try:
            for line in path.read_text().splitlines():
                self.ui.line("  " + plain(line))
        except FileNotFoundError:
            self.ui.line("  No notes yet.")
        line = self.ui.ask("  Append a note (Enter to return; do not store passwords): ")
        if line:
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 12000:
                raise ValueError("Notebook full; archive it before adding notes")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, 'a') as file:
                file.write(time.strftime('%Y-%m-%d %H:%M ') + plain(line[:1000]) + '\n')
            self.ui.ok("Note appended")

    def wake(self):
        mac = self.ui.ask("  MAC address to wake (AA:BB:CC:DD:EE:FF): ")
        if not mac:
            return
        if not re.fullmatch(r'(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', mac) or int(mac[:2], 16) & 1 or mac == '00:00:00:00:00:00':
            raise ValueError("Use the target NIC's unicast MAC address")
        if not self.console.confirm(f"Send a Wake-on-LAN packet to {mac}?"):
            return
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(3)
            sock.sendto(b'\xff' * 6 + bytes.fromhex(mac.replace(':', '')) * 16, ('255.255.255.255', 9))
        self.ui.ok("Wake packet sent; host startup is not confirmed")

    def export(self):
        data = request()
        path = Path('/etc/kvmd/user/kvmcontrol-schedule-export.json')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w') as file:
            json.dump(data, file, indent=2)
        self.ui.ok(f"Schedules and activity exported to {path}")
