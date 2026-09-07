#!/usr/bin/env python3
"""Interactive ATX console for GL.iNet Comet (GL-RM1) + GL-ATXPC.

This program simulates the attached PC's physical front-panel buttons through
``atxpower``.  It does not control power to the KVM appliance itself.

Hard power-off and reset require confirmation. Optional persistent automation
permits only graceful on/off and Wake-on-LAN, and starts paused.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import shutil
from dataclasses import dataclass
from typing import TextIO


DEFAULT_ATXPOWER = "/usr/sbin/atxpower"
DEFAULT_DEVICE = "/dev/ttyACM0"
MENU_LABEL_WIDTH = 16
MENU_WIDTH = 72  # badges right-align here regardless of hint length


@dataclass(frozen=True)
class Palette:
    """Synthwave colors, disabled automatically when output is not a TTY."""

    enabled: bool
    theme: str = "synthwave"

    @property
    def reset(self) -> str:
        return "\x1b[0m" if self.enabled else ""

    @property
    def bold(self) -> str:
        return "\x1b[1m" if self.enabled else ""

    @property
    def italic(self) -> str:
        return "\x1b[3m" if self.enabled else ""

    def rgb(self, red: int, green: int, blue: int) -> str:
        if not self.enabled:
            return ""
        return f"\x1b[38;2;{red};{green};{blue}m"

    @property
    def pink(self) -> str:
        return self.rgb(255, 41, 125)

    @property
    def magenta(self) -> str:
        return self.rgb(*{
            "aurora": (105,153,255), "ember": (255,77,109), "ice": (185,180,255),
        }.get(self.theme, (242,34,255)))

    @property
    def purple(self) -> str:
        return self.rgb(157, 78, 221)

    @property
    def cyan(self) -> str:
        return self.rgb(*{
            "aurora": (117,243,196), "ember": (255,181,97), "ice": (147,219,255),
        }.get(self.theme, (5,217,232)))

    @property
    def orange(self) -> str:
        return self.rgb(255, 140, 66)

    @property
    def yellow(self) -> str:
        return self.rgb(255, 209, 0)

    @property
    def green(self) -> str:
        return self.rgb(57, 255, 145)

    @property
    def red(self) -> str:
        return self.rgb(255, 51, 102)

    @property
    def muted(self) -> str:
        return self.rgb(138, 120, 172)

    @property
    def white(self) -> str:
        return "\x1b[97m" if self.enabled else ""


class ConsoleUI:
    """Terminal presentation. Arrow keys when stdin and stdout are a TTY."""

    def __init__(self, input_stream: TextIO, output_stream: TextIO) -> None:
        self.input = input_stream
        self.output = output_stream
        self.is_tty = bool(getattr(output_stream, "isatty", lambda: False)())
        term = os.environ.get("TERM", "")
        color = self.is_tty and term.lower() != "dumb" and "NO_COLOR" not in os.environ
        try:
            from automation.terminal_design import load
            preferences = load()
        except ImportError:
            preferences = {"theme": "synthwave", "compact": False, "animations": True, "quick": False}
        self.theme = os.environ.get("ATX_THEME", preferences["theme"])
        self.compact = os.environ.get("ATX_COMPACT") == "1" or preferences["compact"]
        self.quick = os.environ.get("ATX_QUICK") == "1" or preferences.get("quick") is True
        self.animations = preferences.get("animations", True) is True
        self.palette = Palette(color, self.theme)
        self.animate = (
            color
            and self.animations
            and os.environ.get("ATX_NO_ANIMATION") is None
            and not self.quick
        )
        self._entered = False
        self._gradient_tables: dict[int, tuple[str, ...]] = {}

    def write(self, text: str = "", *, flush: bool = False) -> None:
        self.output.write(text)
        if flush:
            self.output.flush()

    def line(self, text: str = "") -> None:
        self.write(text + "\n")

    def ask(self, prompt: str) -> str | None:
        """Read one line, returning None on EOF instead of spinning forever."""
        self.write(prompt, flush=True)
        answer = self.input.readline()
        if answer == "":
            self.line()
            return None
        return answer.rstrip("\r\n")

    def clear(self) -> None:
        if self.is_tty and os.environ.get("TERM", "").lower() != "dumb":
            self.write("\x1b[2J\x1b[H")

    def _gradient(self, width: int) -> tuple[str, ...]:
        """Magenta->cyan color table for `width` cells, cached per width.

        Used by both rule() (every menu redraw) and progress() (every
        animation frame); the RGB math only depends on (index, width,
        palette.enabled) — all fixed once an instance exists — so
        computing it once and reusing avoids O(frames * width) redundant
        interpolation.
        """
        width = max(width, 0)
        key = (width, self.theme)
        table = self._gradient_tables.get(key)
        if table is None:
            start, end = {
                "aurora": ((105, 153, 255), (117, 243, 196)),
                "ember": ((255, 77, 109), (255, 181, 97)),
                "ice": ((185, 180, 255), (147, 219, 255)),
            }.get(self.theme, ((242, 34, 255), (5, 217, 232)))
            denom = max(width, 1)
            colors = []
            for offset in range(width):
                t = offset / denom
                red = round(start[0] + (end[0] - start[0]) * t)
                green = round(start[1] + (end[1] - start[1]) * t)
                blue = round(start[2] + (end[2] - start[2]) * t)
                colors.append(self.palette.rgb(red, green, blue))
            table = tuple(colors)
            self._gradient_tables[key] = table
        return table

    def rule(self, width: int = 50) -> None:
        if self.palette.enabled:
            body = "".join(f"{color}─" for color in self._gradient(width))
            self.line(f"  {body}{self.palette.reset}")
        else:
            self.line("  " + "-" * width)

    def progress(self, label: str, seconds: float) -> None:
        """Render a cheap in-process animation, or a plain note when redirected."""
        p = self.palette
        self.line()
        self.line(f"  {p.muted}{label}{p.reset}")
        if not self.animate or self.quick or seconds <= 0:
            return

        width = 30
        gradient = self._gradient(width)
        started = time.monotonic()
        try:
            while True:
                elapsed = min(time.monotonic() - started, seconds)
                fraction = elapsed / seconds
                filled = round(width * fraction)
                blocks = [
                    f"{gradient[i]}█" if i < filled else f"{p.muted}░"
                    for i in range(width)
                ]
                percent = round(fraction * 100)
                frame = "".join(blocks)
                self.write(
                    f"\r  {p.purple}▐{p.reset}{frame}{p.reset}"
                    f"{p.purple}▌{p.reset} {p.white}{percent:3d}%{p.reset}  ",
                    flush=True,
                )
                if elapsed >= seconds:
                    break
                time.sleep(min(0.1, seconds - elapsed))
        finally:
            self.line(p.reset)

    def ok(self, message: str) -> None:
        p = self.palette
        self.line(f"  {p.green}▸{p.reset} {message}")

    def warn(self, message: str) -> None:
        p = self.palette
        self.line(f"  {p.yellow}▸{p.reset} {message}")

    def error(self, message: str) -> None:
        p = self.palette
        self.line(f"  {p.red}▸{p.reset} {message}")

    def brief_delay(self) -> None:
        if self.animate:
            time.sleep(1)


@dataclass(frozen=True)
class Settings:
    atxpower: str
    device: str
    host: str
    target_label: str

    @classmethod
    def from_environment(cls) -> "Settings":
        try:
            host = socket.gethostname() or "unknown"
        except OSError:
            host = "unknown"
        return cls(
            atxpower=os.environ.get("ATXPOWER") or DEFAULT_ATXPOWER,
            device=os.environ.get("ATX_DEVICE") or DEFAULT_DEVICE,
            host=host,
            target_label=os.environ.get("ATX_TARGET_LABEL", ""),
        )


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    output: str


@dataclass(frozen=True)
class MenuItem:
    """One menu row. Drives both the printed menu and the dispatch table,
    so adding/editing an action means editing MENU_ITEMS once instead of
    keeping a hand-formatted menu() string and a separate dispatch dict
    in sync by hand."""

    key: str
    label: str
    hint: str
    section: str
    badge: str
    badge_color: str  # Palette attribute name, e.g. "cyan"
    handler: str  # ATXConsole method name


MENU_ITEMS: tuple[MenuItem, ...] = (
    MenuItem("1", "status", "read power_state (on / off / sleep)",
             "POWER", "READ", "cyan", "show_status"),
    MenuItem("2", "power on", "graceful start if off",
             "POWER", "START", "cyan", "do_on"),
    MenuItem("3", "power off", "ACPI / short press — preferred off",
             "POWER", "ACPI", "cyan", "do_off"),
    MenuItem("4", "power off HARD", "long press — last resort",
             "POWER", "DANGER", "red", "do_off_hard"),
    MenuItem("5", "reset", "reset header — hard reboot",
             "POWER", "HARD", "yellow", "do_reset"),
    MenuItem("6", "raw short click", "no on/off check",
             "RAW / INFO", "RAW", "cyan", "do_click_short"),
    MenuItem("7", "raw long click", "no on/off check",
             "RAW / INFO", "RAW-HOLD", "red", "do_click_long"),
    MenuItem("8", "raw reset click", "no on/off check",
             "RAW / INFO", "RAW", "cyan", "do_click_reset"),
    MenuItem("9", "board serial", "get_sn",
             "RAW / INFO", "READ", "cyan", "do_serial"),
)


EXTRA_ITEMS = (
    ("f", "fleet / switch target", "Both KVMs, one command deck", "FLEET & WORKFLOWS", "FLEET"),
    ("r", "guided recovery", "Shutdown → confirm off → start", "FLEET & WORKFLOWS", "GUIDED"),
    ("v", "watch transitions", "Live phase and countdown display", "FLEET & WORKFLOWS", "LIVE"),
    ("c", "cancel workflow", "Stop remaining recovery steps", "FLEET & WORKFLOWS", "STOP"),
    ("a", "add schedule", "Calendar, weekdays, weekends, timers", "AUTOMATION", "TIMER"),
    ("l", "manage queue", "Pause / resume / snooze / skip", "AUTOMATION", "QUEUE"),
    ("m", "maintenance presets", "Save and reuse operation templates", "AUTOMATION", "PRESET"),
    ("p", "PAUSE ALL", "Pause schedules and cancel workflow", "AUTOMATION", "STOP"),
    ("e", "enable scheduling", "Review and arm this target", "AUTOMATION", "ARM"),
    ("h", "activity history", "Confirmed states and outcomes", "OPERATIONS", "LOG"),
    ("w", "Wake-on-LAN", "Wake a configured LAN adapter", "OPERATIONS", "WAKE"),
    ("d", "diagnostics", "Inventory and clock information", "OPERATIONS", "READ"),
    ("n", "notebook", "Append local recovery notes", "OPERATIONS", "NOTES"),
    ("s", "settings & alerts", "Timing and optional HTTPS webhook", "OPERATIONS", "CONFIG"),
    ("b", "backup", "Export schedules, presets, settings", "OPERATIONS", "SAVE"),
    ("i", "restore backup", "Preview → confirm → import paused", "OPERATIONS", "RESTORE"),
    ("x", "export activity", "Save a JSON audit snapshot", "OPERATIONS", "EXPORT"),
    ("t", "appearance", "Theme, compact mode, animations", "OPERATIONS", "LOOK"),
    ("/", "search", "Find a command by name", "OPERATIONS", "FIND"),
)


class ATXConsole:
    def __init__(self, settings: Settings, ui: ConsoleUI) -> None:
        self.settings = settings
        self.ui = ui
        self.target_id = None
        self.target_label = settings.target_label or settings.host
        self.remote_target = False
        self.snapshot = None
        self.menu_index = 0

    def request(self, path="/scheduler", body=None):
        if self.target_id is None:
            from automation.client import request
            return request(path, body)
        from automation.fleet import call
        return call(self.target_id, path, body)

    def banner(self, *, fetch: bool = True) -> None:
        try:
            from automation.terminal_design import header, entrance
            if not self.ui._entered:
                entrance(self.ui)
                self.ui._entered = True
            if fetch:
                try:
                    self.snapshot = self.request()
                except Exception:
                    self.snapshot = None
            header(self, self.snapshot)
        except ImportError:
            self.ui.line("GLKVM / " + self.target_label)

    def board_present(self) -> bool:
        return os.path.exists(self.settings.device)

    def tool_available(self) -> bool:
        return os.access(self.settings.atxpower, os.X_OK)

    def need_board(self) -> bool:
        p = self.ui.palette
        if not self.board_present():
            self.ui.error(f"ATX board not found at {self.settings.device}")
            self.ui.line(
                f"  {p.muted}Plug the GL-ATXPC USB-C into the KVM "
                f"USB-Device (not 5V 2A).{p.reset}"
            )
            self.ui.line(
                f"  {p.muted}Need a data cable — charge-only A-to-C will "
                f"light the LED and still fail.{p.reset}"
            )
            return False
        if not self.tool_available():
            self.ui.error(f"missing {self.settings.atxpower}")
            return False
        return True

    @staticmethod
    def _decode(output: bytes | None) -> str:
        return (output or b"").decode("utf-8", errors="replace").replace("\r", "")

    def invoke(self, command: str, *, combine_stderr: bool) -> CommandResult:
        stderr: int = subprocess.STDOUT if combine_stderr else subprocess.DEVNULL
        try:
            completed = subprocess.run(
                [self.settings.atxpower, self.settings.device, command],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=stderr,
                check=False,
                timeout=8,
            )
        except subprocess.TimeoutExpired:
            return CommandResult(124, "ATX command timed out; inspect the host before retrying")
        except OSError as exc:
            return CommandResult(126, str(exc))
        return CommandResult(completed.returncode, self._decode(completed.stdout))

    def read_state(self) -> str:
        if not self.board_present():
            return "NO-BOARD"
        result = self.invoke("power_state", combine_stderr=False)
        if result.returncode != 0:
            return "UNKNOWN"
        for line in result.output.splitlines():
            fields = line.split()
            if fields:
                return fields[0]
        return ""

    def show_status(self) -> None:
        p = self.ui.palette
        self.ui.line(f"  {p.bold}reading board…{p.reset}")
        self.ui.progress("UART  power_state", 2)
        state = self.read_state()
        self.ui.line()
        if not self.board_present():
            self.ui.error(f"board absent  ({self.settings.device} missing)")
            return
        self.ui.ok(f"device  {self.settings.device} present")
        if not self.tool_available():
            self.ui.error(f"cannot read state: missing {self.settings.atxpower}")
            return
        if state == "on":
            self.ui.ok(
                f"PC power  {p.green}{p.bold}ON{p.reset}   "
                "(ACPI / running or still shutting down)"
            )
        elif state == "off":
            self.ui.warn(
                f"PC power  {p.red}{p.bold}OFF{p.reset}  "
                "(rails down — short click will start it)"
            )
        elif state == "sleep":
            self.ui.warn(
                f"PC power  {p.yellow}{p.bold}SLEEP{p.reset}  "
                "(S3/S4-ish — short click usually wakes)"
            )
        else:
            self.ui.warn(f"PC power  unknown ({state or 'empty'})")

    def confirm(self, prompt: str) -> bool:
        p = self.ui.palette
        self.ui.line(f"  {p.cyan}TARGET: {self.target_label}{p.reset}")
        try:
            from automation.tui_nav import confirm as choose
            return choose(self.ui, prompt)
        except ImportError:
            answer = self.ui.ask(f"  {p.yellow}{prompt}{p.reset}  [y/N] ")
            if answer in {"y", "Y", "yes", "YES"}:
                return True
            suffix = " (input closed)" if answer is None else ""
            self.ui.line(f"  {p.muted}cancelled{suffix}{p.reset}")
            return False

    def run_atx(self, command: str, wait_seconds: int, title: str) -> None:
        p = self.ui.palette
        self.ui.line()
        self.ui.line(f"  {p.bold}what will happen{p.reset}")
        self.ui.line(f"  {p.muted}{title}{p.reset}")
        self.ui.line()
        self.ui.line(
            f"  {p.cyan}→{p.reset} {self.settings.atxpower} "
            f"{self.settings.device} {command}"
        )
        self.ui.line()
        if not self.need_board():
            return

        result = self.invoke(command, combine_stderr=True)
        self.ui.progress(f"sending {command}  /  waiting for board", wait_seconds)
        state = self.read_state()

        self.ui.line()
        if result.returncode == 0:
            self.ui.ok(f"board accepted {command}")
        else:
            self.ui.error(f"atxpower exited {result.returncode}")
            for line in result.output.splitlines():
                self.ui.line(f"  {p.muted}{line}{p.reset}")
        self.ui.line(
            f"  {p.bold}reported state now:{p.reset}  "
            f"{p.white}{state or '?'}{p.reset}"
        )
        self.ui.line()
        self.ui.line(
            f"  {p.muted}ON/OFF in software can lag a few seconds behind ACPI.{p.reset}"
        )

    def do_on(self) -> None:
        p = self.ui.palette
        self.ui.line(
            f"  {p.green}POWER ON{p.reset} — short power-button pulse "
            "if the PC is off."
        )
        self.ui.line(
            f"  {p.muted}No-op if atxpower already sees 'on'. "
            f"Does not force a hard reset.{p.reset}"
        )
        if self.confirm(f"Pulse power ON on the machine attached to {self.target_label}?"):
            self.run_atx(
                "power_on",
                3,
                "Short click. Same as tapping the case power button from off.",
            )

    def do_off(self) -> None:
        p = self.ui.palette
        self.ui.line(
            f"  {p.yellow}GRACEFUL OFF{p.reset} — short power-button pulse "
            "while the PC is on."
        )
        self.ui.line(
            f"  {p.muted}Asks the OS to shut down (ACPI). "
            f"May sit in 'on' until userspace exits.{p.reset}"
        )
        self.ui.line(
            f"  {p.red}If the attached PC is the machine you're SSHed in from, "
            f"this ends your session too.{p.reset}"
        )
        if self.confirm("Ask the attached PC to shut down (graceful)?"):
            self.run_atx(
                "power_off",
                4,
                "Short click while on. Same as a normal press of the power button.",
            )

    def do_off_hard(self) -> None:
        p = self.ui.palette
        self.ui.line(
            f"  {p.red}{p.bold}HARD OFF{p.reset} — long power-button hold. "
            "Cuts power. Unsaved work is gone."
        )
        self.ui.line(f"  {p.red}Use only if the OS is wedged.{p.reset}")
        if not self.confirm("FORCE the attached PC off (long press)?"):
            return
        if not self.confirm("Type y again — this is not ACPI, it is a hold."):
            return
        self.run_atx(
            "power_off_hard",
            5,
            "Long press. Equivalent to holding the case power button.",
        )

    def do_reset(self) -> None:
        p = self.ui.palette
        self.ui.line(
            f"  {p.yellow}RESET{p.reset} — pulses the motherboard reset header "
            "(not a clean reboot)."
        )
        self.ui.line(
            f"  {p.muted}Instant restart. Filesystems may need journal replay.{p.reset}"
        )
        if self.confirm("Pulse RESET on the attached PC?"):
            self.run_atx(
                "power_reset",
                3,
                "Reset header click. Hard reboot, not shutdown-then-on.",
            )

    def do_click_short(self) -> None:
        p = self.ui.palette
        self.ui.line(
            f"  {p.white}RAW short click{p.reset} — always send a short pulse, no on/off logic."
        )
        if self.confirm("Send click_power_short?"):
            self.run_atx(
                "click_power_short",
                3,
                "Unconditional short POWER SW pulse.",
            )

    def do_click_long(self) -> None:
        p = self.ui.palette
        self.ui.line(
            f"  {p.white}RAW long click{p.reset} — always send a long pulse "
            "(hard-off if the PC is on)."
        )
        if self.confirm("Send click_power_long?"):
            self.run_atx(
                "click_power_long",
                5,
                "Unconditional long POWER SW hold.",
            )

    def do_click_reset(self) -> None:
        p = self.ui.palette
        self.ui.line(f"  {p.white}RAW reset click{p.reset} — pulse RESET SW only.")
        if self.confirm("Send click_reset?"):
            self.run_atx("click_reset", 3, "Unconditional RESET SW pulse.")

    def do_serial(self) -> None:
        p = self.ui.palette
        self.ui.line(f"  {p.bold}board serial{p.reset}")
        self.ui.progress("UART  get_sn", 2)
        self.ui.line()
        if not self.need_board():
            return
        result = self.invoke("get_sn", combine_stderr=True)
        for line in result.output.splitlines():
            self.ui.line(f"  {line}")
        if result.returncode != 0:
            self.ui.error(f"atxpower exited {result.returncode}")

    def menu(self) -> str | None:
        from automation.terminal_design import menu
        items = [(item.key, item.label, item.hint, item.section, item.badge) for item in MENU_ITEMS]
        extras = [(key, label, hint, group, badge) for key, label, hint, group, badge in EXTRA_ITEMS]
        return menu(self, items, extras)

    def pause(self) -> bool:
        try:
            from automation.tui_nav import wait_for_key
            return wait_for_key(self.ui)
        except Exception:
            p = self.ui.palette
            return self.ui.ask(f"  {p.muted}enter to return to menu{p.reset} ") is not None

    def goodbye(self, reason: str = "bye") -> None:
        p = self.ui.palette
        self.ui.line(f"  {p.muted}{reason}{p.reset}")
        self.ui.line()

    def run(self) -> int:
        actions = {item.key: getattr(self, item.handler) for item in MENU_ITEMS}
        def extra(action):
            try:
                from automation.console_tools import ConsoleTools
                ConsoleTools(self).run(action)
            except ImportError:
                self.ui.error("Install the automation directory beside atx_console.py to use these tools")
        for key, action in {"a": "add", "l": "queue", "p": "pause", "e": "enable", "h": "history",
                            "w": "wake", "d": "diagnostics", "n": "notes", "x": "export",
                            "f": "fleet", "m": "presets", "r": "recover", "c": "cancel",
                            "v": "watch", "b": "backup", "i": "restore", "s": "settings", "t": "appearance", "/": "search"}.items():
            actions[key] = lambda action=action: extra(action)
        while True:
            try:
                self.snapshot = self.request()
            except Exception:
                self.snapshot = None
            try:
                from automation.tui_nav import interactive
                tty_menu = interactive(self.ui)
            except ImportError:
                tty_menu = False
            if not tty_menu:
                self.banner(fetch=False)
                if not self.remote_target and not self.need_board():
                    self.ui.warn(
                        "actions that talk to the board will fail until it enumerates"
                    )
                self.ui.line()
            choice = self.menu()
            if choice is None:
                self.goodbye("input closed — bye")
                return 0
            self.ui.line()
            normalized = choice.strip().lower()
            if normalized in {"tab", "j"}:
                self.ui.quick = not self.ui.quick
                self.ui.animate = (
                    self.ui.palette.enabled
                    and self.ui.animations
                    and os.environ.get("ATX_NO_ANIMATION") is None
                    and not self.ui.quick
                )
                self.menu_index = 0
                continue
            if normalized in {"q", "0", "quit", "exit"}:
                self.goodbye()
                return 0
            action = actions.get(normalized)
            if action is None:
                self.ui.warn("not a menu item")
                self.ui.brief_delay()
                continue
            if self.remote_target and normalized in {item.key for item in MENU_ITEMS}:
                if normalized in ("1", "2", "3"):
                    from automation.console_tools import ConsoleTools
                    ConsoleTools(self).run({"1": "remote_status", "2": "remote_on", "3": "remote_off"}[normalized])
                else:
                    self.ui.warn("Raw and hard controls are local-only. Open that KVM's SSH console to use them.")
            else:
                action()
            if not self.pause():
                self.goodbye("input closed — bye")
                return 0


def main() -> int:
    ui = ConsoleUI(sys.stdin, sys.stdout)
    console = ATXConsole(Settings.from_environment(), ui)
    try:
        return console.run()
    except KeyboardInterrupt:
        try:
            ui.line()
            ui.warn("interrupted; a hardware command already in progress may have been sent")
        except BrokenPipeError:
            pass
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
