# SPDX-License-Identifier: GPL-3.0-or-later
"""Line-oriented tools; importing this module performs no hardware I/O."""
import datetime
import json
import os
from pathlib import Path
import re
import time

from automation import terminal_design


def plain(value):
    """Never replay terminal control characters from saved names or logs."""
    return "".join(char for char in str(value) if char.isprintable())


def display_time(value):
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%Y-%m-%d %H:%M %Z")


class ConsoleTools:
    def __init__(self, console):
        self.console = console
        self.ui = console.ui

    def api(self, path="/scheduler", body=None):
        return self.console.request(path, body)

    def hostname(self):
        snapshot = getattr(self.console, "snapshot", None) or {}
        if snapshot.get("target"):
            return snapshot["target"]
        return self.api("/scheduler/status")["target"]

    def run(self, action):
        try:
            getattr(self, action)()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError) as error:
            self.ui.error(plain(error))
            self.ui.warn("Automation needs the kvmcontrol service installed and running on the selected target.")

    def overview(self):
        state = self.api()
        self.console.snapshot = state
        locked = "LOCKED" if state["error"] else "ARMED" if state["armed"] else "PAUSED"
        self.ui.line(f"  TARGET  /  {plain(state.get('target', self.console.target_label))}  /  {locked}")
        if state["error"]:
            self.ui.error(state["error"])
        jobs = sorted(state["jobs"], key=lambda job: job["at"])
        if not jobs:
            self.ui.line("  No schedules. Choose [a] to create one.")
        for number, job in enumerate(jobs, 1):
            status = "QUEUED" if job["enabled"] else "INACTIVE"
            self.ui.line(f"  {number:>2}. [{status}] {plain(job['name'])} / {job['action']}")
            self.ui.line(f"      {display_time(job['at'])} · {job['repeat']} · {job['id'][:8]}")
        active = state.get("active")
        if active:
            self.ui.warn(f"Active workflow: {plain(active.get('phase', 'running'))} — [c] cancel remaining steps")
        return jobs, state

    def queue(self):
        jobs, _state = self.overview()
        if not jobs:
            return
        answer = self.ui.ask("  Job number to manage (Enter to return): ")
        if not answer:
            return
        index = int(answer) - 1
        if index < 0 or index >= len(jobs):
            raise ValueError("Choose a displayed job number")
        job = jobs[index]
        operation = self.ui.ask("  pause / resume / delete / snooze / skip (Enter to cancel): ")
        if operation not in ("pause", "resume", "delete", "snooze", "skip"):
            return
        body = {"id": job["id"], "operation": operation}
        if operation == "snooze":
            minutes = self.ui.ask("  Snooze minutes [15]: ") or "15"
            body["minutes"] = int(minutes)
            if not 1 <= body["minutes"] <= 10080:
                raise ValueError("Snooze must be 1–10080 minutes")
        if self.console.confirm(f"{operation.title()} schedule '{plain(job['name'])}' on {self.console.target_label}?"):
            self.api("/scheduler/job", body)
            self.ui.ok("Schedule updated on " + self.console.target_label)

    def add(self):
        state = self.api()
        name = self.ui.ask("  Schedule name (Enter to cancel): ")
        if not name:
            return
        action = self.ui.ask("  Action: on / off / wol / recover: ")
        if action not in ("on", "off", "wol", "recover"):
            raise ValueError("Choose on, off, wol, or recover")
        mac = self.ui.ask("  Target MAC (AA:BB:CC:DD:EE:FF): ") if action == "wol" else ""
        self.ui.line("  First run: +15m, +2h, +1d, or ISO date/time with offset")
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
        timezone = self.ui.ask(f"  IANA time zone [{state.get('settings', {}).get('timezone', 'UTC')}]: ") or state.get("settings", {}).get("timezone", "UTC")
        repeat = self.ui.ask("  Repeat: once / daily / weekly / calendar / weekdays / weekends [once]: ") or "once"
        if repeat not in ("once", "daily", "weekly", "calendar", "weekdays", "weekends"):
            raise ValueError("Invalid recurrence")
        body = {"name": name, "action": action, "at": due.isoformat(), "repeat": repeat, "mac": mac or "", "timezone": timezone}
        if repeat in ("calendar", "weekdays", "weekends"):
            wall = self.ui.ask(f"  Local time HH:MM [{due.astimezone().strftime('%H:%M')}]: ") or due.astimezone().strftime("%H:%M")
            body["wall_time"] = wall
        if repeat == "calendar":
            days = self.ui.ask("  Weekdays Monday=0 … Sunday=6, comma-separated [0,1,2,3,4]: ") or "0,1,2,3,4"
            body["days"] = [int(item.strip()) for item in days.split(",") if item.strip()]
        excluded = self.ui.ask("  Excluded dates YYYY-MM-DD, comma-separated (optional): ")
        if excluded:
            body["exceptions"] = [item.strip() for item in excluded.split(",") if item.strip()]
        self.ui.line(f"  TARGET {self.console.target_label} · {action.upper()} · {display_time(body['at'])} · {body['repeat']}")
        if repeat in ("once", "daily", "weekly"):
            self.ui.warn("once/daily/weekly use fixed UTC intervals; local hour shifts with daylight saving.")
        else:
            self.ui.warn("Civil-time jobs skip missing spring-forward times and use the first fall-back hour.")
        if state["armed"]:
            self.ui.warn("Scheduling is armed on this target: the job will run without an open terminal.")
        if self.console.confirm(f"Save this power schedule on {self.console.target_label}?"):
            self.api("/scheduler/jobs", body)
            self.ui.ok("Saved on " + self.console.target_label + ("" if state["armed"] else "; scheduler remains paused"))

    def pause(self):
        self.api("/scheduler/armed", {"armed": False})
        try:
            self.api("/scheduler/cancel", {})
        except RuntimeError:
            pass
        self.ui.ok("Scheduling paused on " + self.console.target_label + ". Already dispatched commands may still finish.")

    def enable(self):
        self.overview()
        self.ui.warn(f"These jobs run on {self.console.target_label} even after you disconnect. Check clock and target first.")
        if self.console.confirm(f"Enable scheduled power operations on {self.console.target_label}?"):
            self.api("/scheduler/armed", {"armed": True})
            self.ui.ok("Scheduling enabled on " + self.console.target_label)

    def history(self):
        state = self.api()
        if not state["history"]:
            self.ui.line("  No scheduled activity yet.")
        for event in state["history"][:30]:
            self.ui.line(f"  {display_time(event['time'])} [{plain(event['status']).upper()}] {plain(event['name'])}")
            self.ui.line("    " + plain(event["detail"]))

    def diagnostics(self):
        settings = self.console.settings
        self.ui.line("  LOCAL INVENTORY  /  this SSH session, not the selected fleet target")
        self.ui.line(f"  KVM hostname: {plain(settings.host)}")
        self.ui.line(f"  Selected target: {plain(self.console.target_label)}")
        self.ui.line(f"  Clock: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        self.ui.line(f"  ATX tool: {'present' if self.console.tool_available() else 'missing'}")
        self.ui.line(f"  ATX device node: {'present' if self.console.board_present() else 'missing'}")
        try:
            seconds = float(Path("/proc/uptime").read_text().split()[0])
            self.ui.line(f"  Uptime: {int(seconds // 86400)}d {int(seconds % 86400 // 3600)}h")
        except (OSError, ValueError):
            pass
        try:
            usage = os.statvfs("/etc/kvmd/user")
            self.ui.line(f"  User storage free: {usage.f_bavail * usage.f_frsize // 1048576} MiB")
        except OSError:
            pass
        try:
            state = self.api()
            self.ui.line(f"  Selected automation: {'LOCKED' if state['error'] else 'armed' if state['armed'] else 'paused'}")
            self.ui.line(f"  Saved schedules: {len(state['jobs'])}")
            self.ui.line(f"  Selected hostname: {plain(state.get('target', ''))}")
        except OSError:
            self.ui.warn("Automation service unavailable on the selected target")
        self.ui.line("  Web dashboard: https://<this-kvm>/command/")
        self.ui.line("  No power, USB, video, or network operation was performed.")

    def notes(self):
        path = Path("/etc/kvmd/user/kvmcontrol-notes.txt")
        self.ui.line("  NOTEBOOK / stored on THIS KVM, not a remote fleet target")
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
            with os.fdopen(fd, "a") as file:
                file.write(time.strftime("%Y-%m-%d %H:%M ") + plain(line[:1000]) + "\n")
            self.ui.ok("Note appended on this KVM")

    def wake(self):
        mac = self.ui.ask("  MAC address to wake (AA:BB:CC:DD:EE:FF): ")
        if not mac:
            return
        if not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac) or int(mac[:2], 16) & 1 or mac.lower() == "00:00:00:00:00:00":
            raise ValueError("Use the target NIC's unicast MAC address")
        host = self.hostname()
        if not self.console.confirm(f"Send a tracked Wake-on-LAN packet from {self.console.target_label} ({host}) to {mac}?"):
            return
        result = self.api("/scheduler/run", {"action": "wol", "mac": mac, "confirm_target": host})
        self.ui.ok("Wake accepted as workflow " + result.get("id", "")[:8] + "; host startup is not confirmed")

    def export(self):
        data = self.api("/scheduler/backup")
        path = Path("/etc/kvmd/user/kvmcontrol-schedule-export.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, indent=2)
        self.ui.ok(f"Secret-free backup written on THIS KVM to {path}")

    def fleet(self):
        from automation import fleet as fleet_mod
        self.ui.line("  FLEET  /  existing SSH trust only  /  no new keys")
        targets = fleet_mod.overview()
        for number, target in enumerate(targets, 1):
            state = target.get("state") or {}
            error = target.get("error")
            power = state.get("power", "?") if state else "unreachable"
            self.ui.line(f"  {number:>2}. {plain(target['id'])}  {plain(target['label'])}")
            self.ui.line(f"      host={plain(target.get('host') or 'local')}  power={plain(power)}  {plain(error or '')}")
        answer = self.ui.ask("  Select target number (Enter to keep current): ")
        if not answer:
            return
        index = int(answer) - 1
        if index < 0 or index >= len(targets):
            raise ValueError("Choose a displayed target number")
        chosen = targets[index]
        self.console.target_id = chosen["id"]
        self.console.target_label = chosen["label"]
        self.console.remote_target = bool(chosen.get("host"))
        self.console.snapshot = None
        self.ui.ok("Command target is now " + chosen["label"] + (" (remote via SSH)" if self.console.remote_target else " (local)"))
        if self.console.remote_target:
            self.ui.warn("Raw/hard ATX clicks stay on this KVM. Power on/off/status use the selected target's tracked scheduler.")

    def presets(self):
        state = self.api()
        items = state.get("presets", [])
        if not items:
            self.ui.line("  No presets saved.")
        for number, preset in enumerate(items, 1):
            self.ui.line(f"  {number:>2}. {plain(preset['name'])} · {preset['action']} · {preset['repeat']}")
        operation = self.ui.ask("  save / apply / delete (Enter to return): ")
        if operation == "delete":
            name = self.ui.ask("  Preset name to delete: ")
            if name and self.console.confirm(f"Delete preset '{plain(name)}' on {self.console.target_label}?"):
                self.api("/scheduler/presets", {"operation": "delete", "name": name})
                self.ui.ok("Preset deleted")
        elif operation == "save":
            name = self.ui.ask("  Preset name: ")
            action = self.ui.ask("  Action on / off / wol / recover: ")
            repeat = self.ui.ask("  Repeat once / daily / weekly / calendar / weekdays / weekends [once]: ") or "once"
            timezone = self.ui.ask(f"  Time zone [{state.get('settings', {}).get('timezone', 'UTC')}]: ") or state.get("settings", {}).get("timezone", "UTC")
            wall = self.ui.ask("  Local time HH:MM [08:00]: ") or "08:00"
            body = {
                "name": name,
                "action": action,
                "repeat": repeat,
                "timezone": timezone,
                "wall_time": wall,
                "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "mac": self.ui.ask("  MAC if wol (else Enter): ") or "",
            }
            if self.console.confirm(f"Save preset '{plain(name)}' on {self.console.target_label}?"):
                self.api("/scheduler/presets", body)
                self.ui.ok("Preset saved")
        elif operation == "apply":
            name = self.ui.ask("  Preset name to schedule: ")
            preset = next((item for item in items if item["name"] == name), None)
            if preset is None:
                raise ValueError("No preset with that name")
            when = self.ui.ask("  First execution (+15m or ISO with offset): ")
            if not when:
                return
            match = re.fullmatch(r"\+(\d{1,6})([mhd])", when.strip())
            if match:
                seconds = int(match[1]) * {"m": 60, "h": 3600, "d": 86400}[match[2]]
                due = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
            else:
                due = datetime.datetime.fromisoformat(when.replace("Z", "+00:00"))
                if due.tzinfo is None:
                    raise ValueError("Include an explicit UTC offset")
            job = {key: preset[key] for key in ("name", "action", "repeat", "mac", "timezone", "wall_time", "days", "exceptions") if key in preset}
            job["at"] = due.isoformat()
            if self.console.confirm(f"Create schedule from preset '{plain(name)}' on {self.console.target_label}?"):
                self.api("/scheduler/jobs", job)
                self.ui.ok("Schedule created from preset")

    def recover(self):
        host = self.hostname()
        self.ui.warn("Guided recovery: graceful off, confirm off twice, settle, then power on.")
        self.ui.warn("OS health is not inferred. Cancellation cannot undo a command already sent.")
        if not self.console.confirm(f"Start tracked recovery on {self.console.target_label} ({host})?"):
            return
        result = self.api("/scheduler/run", {"action": "recover", "confirm_target": host})
        self.ui.ok("Recovery accepted as workflow " + result.get("id", "")[:8] + " — [v] watch  [c] cancel")

    def cancel(self):
        if not self.console.confirm(f"Cancel remaining workflow steps on {self.console.target_label}?"):
            return
        result = self.api("/scheduler/cancel", {})
        self.ui.ok(plain(result.get("message", "Cancellation requested")))

    def watch(self):
        self.ui.line("  LIVE WATCH  /  30 samples, 2s apart  /  no commands sent")
        for sample in range(30):
            state = self.api()
            active = state.get("active")
            jobs = [job for job in state.get("jobs", []) if job.get("enabled")]
            jobs.sort(key=lambda job: job["at"])
            phase = plain(active.get("phase", "running")) if active else "idle"
            power = plain((active or {}).get("observed_power", ""))
            line = f"  {time.strftime('%H:%M:%S')}  {plain(state.get('target', self.console.target_label))}  {phase}"
            if power:
                line += "  power=" + power
            if jobs and state.get("armed"):
                from automation.calendar_rules import stamp
                seconds = max(0, int(stamp(jobs[0]["at"]) - time.time()))
                line += f"  next={plain(jobs[0]['name'])} in {seconds // 60}m{seconds % 60:02d}s"
            self.ui.line(line)
            if not active and sample >= 2:
                self.ui.ok("No active workflow")
                break
            time.sleep(2)

    def backup(self):
        data = self.api("/scheduler/backup")
        path = Path("/etc/kvmd/user/kvmcontrol-backup.json")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(data, file, indent=2)
        self.ui.ok(f"Backup written on THIS KVM to {path} (webhook URL omitted)")

    def restore(self):
        path = Path(self.ui.ask("  Backup JSON path [/etc/kvmd/user/kvmcontrol-backup.json]: ") or "/etc/kvmd/user/kvmcontrol-backup.json")
        backup = json.loads(path.read_text())
        mode = self.ui.ask("  Restore mode merge / replace [merge]: ") or "merge"
        preview = self.api("/scheduler/restore", {"backup": backup, "mode": mode, "phase": "preview"})
        self.ui.line("  " + plain(preview.get("message", "")))
        self.ui.line(f"  Jobs in preview: {len(preview.get('jobs', []))}  presets: {preview.get('preset_count', 0)}")
        self.ui.warn("Imported jobs stay inactive. Pause scheduling first. Webhook credentials are not restored.")
        if not self.console.confirm(f"Commit this restore on {self.console.target_label}?"):
            return
        self.api("/scheduler/restore", {"backup": backup, "mode": mode, "phase": "commit", "token": preview["token"]})
        self.ui.ok("Restore committed; review the inactive queue before arming")

    def settings(self):
        state = self.api()
        current = state.get("settings", {})
        notices = current.get("notifications", {})
        self.ui.line(f"  Time zone: {plain(current.get('timezone', 'UTC'))}")
        self.ui.line(f"  Warning: {current.get('warning_seconds', 300)}s   Transition timeout: {current.get('transition_timeout', 120)}s")
        self.ui.line(f"  Notifications: {'on' if notices.get('enabled') else 'off'}  webhook={'configured' if notices.get('configured') else 'none'}")
        timezone = self.ui.ask(f"  New time zone [{current.get('timezone', 'UTC')}]: ") or current.get("timezone", "UTC")
        warning = int(self.ui.ask(f"  Warning seconds [{current.get('warning_seconds', 300)}]: ") or current.get("warning_seconds", 300))
        timeout = int(self.ui.ask(f"  Transition timeout [{current.get('transition_timeout', 120)}]: ") or current.get("transition_timeout", 120))
        enabled_answer = self.ui.ask("  Enable HTTPS webhook notifications? y/N: ")
        body = {
            "timezone": timezone,
            "warning_seconds": warning,
            "transition_timeout": timeout,
            "notifications": {"enabled": enabled_answer in {"y", "Y", "yes", "YES"}},
        }
        if body["notifications"]["enabled"]:
            url = self.ui.ask("  HTTPS webhook URL (leave empty to keep current): ")
            if url:
                body["notifications"]["url"] = url
        if self.console.confirm(f"Save settings on {self.console.target_label}? No test notification will be sent."):
            self.api("/scheduler/settings", body)
            self.ui.ok("Settings saved")

    def appearance(self):
        current = terminal_design.load()
        self.ui.line("  Themes: synthwave / aurora / ember / ice")
        theme = self.ui.ask(f"  Theme [{current['theme']}]: ") or current["theme"]
        compact = (self.ui.ask(f"  Compact mode y/N [{'y' if current['compact'] else 'N'}]: ") or ("y" if current["compact"] else "n")).lower().startswith("y")
        animations = (self.ui.ask(f"  Animations Y/n [{'Y' if current['animations'] else 'n'}]: ") or ("y" if current["animations"] else "n")).lower().startswith("y")
        value = {"theme": theme, "compact": compact, "animations": animations}
        if value["theme"] not in terminal_design.THEMES:
            raise ValueError("Unknown theme")
        terminal_design.save(value)
        self.ui.theme = value["theme"]
        self.ui.compact = value["compact"]
        from atx_console import Palette
        self.ui.palette = Palette(self.ui.palette.enabled, value["theme"])
        self.ui.animate = self.ui.palette.enabled and value["animations"] and os.environ.get("ATX_NO_ANIMATION") is None
        self.ui._gradient_tables.clear()
        self.ui.ok("Appearance saved for this console")

    def search(self):
        query = (self.ui.ask("  Search commands: ") or "").strip().lower()
        if not query:
            return
        from atx_console import EXTRA_ITEMS, MENU_ITEMS
        matches = []
        for item in MENU_ITEMS:
            haystack = f"{item.key} {item.label} {item.hint} {item.badge}".lower()
            if query in haystack:
                matches.append((item.key, item.label, item.hint))
        for key, label, hint, _group, _badge in EXTRA_ITEMS:
            if query in f"{key} {label} {hint}".lower():
                matches.append((key, label, hint))
        if query in "appearance theme compact":
            matches.append(("t", "appearance", "Theme, compact, animations"))
        if query in "search find":
            matches.append(("/", "search", "Find a command"))
        if not matches:
            self.ui.warn("No matching commands")
            return
        self.ui.line("  Matches — enter the key at the command prompt")
        for key, label, hint in matches:
            self.ui.line(f"  [{key}] {plain(label)}  {plain(hint)}")

    def remote_status(self):
        state = self.api("/scheduler/status")
        self.ui.line(f"  TARGET {plain(state.get('target', self.console.target_label))}")
        self.ui.line(f"  Power {plain(state.get('power', 'unknown'))}  ATX {'yes' if state.get('atx') else 'no'}")
        self.ui.line(f"  Scheduler {'LOCKED' if state.get('error') else 'armed' if state.get('armed') else 'paused'}  jobs={state.get('jobs', 0)}")
        active = state.get("active")
        if active:
            self.ui.warn("Workflow " + plain(active.get("phase", "running")))
        self.ui.line("  Status only; no power command was sent.")

    def _tracked(self, action, title):
        host = self.hostname()
        self.ui.line(f"  {title}")
        self.ui.line(f"  Tracked scheduler on {self.console.target_label} ({host})")
        if not self.console.confirm(f"{title} on {self.console.target_label} ({host})?"):
            return
        result = self.api("/scheduler/run", {"action": action, "confirm_target": host})
        self.ui.ok("Accepted as workflow " + result.get("id", "")[:8] + " — [v] watch  [c] cancel")

    def remote_on(self):
        self._tracked("on", "POWER ON")

    def remote_off(self):
        self._tracked("off", "GRACEFUL OFF")
