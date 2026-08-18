#!/usr/bin/env python3
"""Interactive ATX console for GL.iNet Comet (GL-RM1) + GL-ATXPC.

This program simulates the attached PC's physical front-panel buttons through
``atxpower``.  It does not control power to the KVM appliance itself.

Hard power-off and reset can lose unsaved work.  Do not run unattended.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import TextIO


DEFAULT_ATXPOWER = "/usr/sbin/atxpower"
DEFAULT_DEVICE = "/dev/ttyACM0"


@dataclass(frozen=True)
class Palette:
    """Synthwave colors, disabled automatically when output is not a TTY."""

    enabled: bool

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
        return self.rgb(242, 34, 255)

    @property
    def purple(self) -> str:
        return self.rgb(157, 78, 221)

    @property
    def cyan(self) -> str:
        return self.rgb(5, 217, 232)

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
    """Line-oriented terminal presentation; no raw mode or cursor input."""

    def __init__(self, input_stream: TextIO, output_stream: TextIO) -> None:
        self.input = input_stream
        self.output = output_stream
        self.is_tty = bool(getattr(output_stream, "isatty", lambda: False)())
        term = os.environ.get("TERM", "")
        color = self.is_tty and term.lower() != "dumb" and "NO_COLOR" not in os.environ
        self.palette = Palette(color)
        self.animate = self.is_tty and os.environ.get("ATX_NO_ANIMATION") is None

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

    def _gradient(self, offset: int, width: int) -> str:
        t = offset / max(width, 1)
        red = round(242 + (5 - 242) * t)
        green = round(34 + (217 - 34) * t)
        blue = round(255 + (232 - 255) * t)
        return self.palette.rgb(red, green, blue)

    def rule(self, width: int = 50) -> None:
        if self.palette.enabled:
            rule = "".join(f"{self._gradient(i, width)}─" for i in range(width))
            self.line(f"  {rule}{self.palette.reset}")
        else:
            self.line("  " + "-" * width)

    def progress(self, label: str, seconds: float) -> None:
        """Render a cheap in-process animation, or a plain note when redirected."""
        p = self.palette
        self.line()
        self.line(f"  {p.muted}{label}{p.reset}")
        if not self.animate or seconds <= 0:
            return

        width = 30
        started = time.monotonic()
        try:
            while True:
                elapsed = min(time.monotonic() - started, seconds)
                fraction = elapsed / seconds
                filled = round(width * fraction)
                blocks = []
                for index in range(width):
                    if index < filled:
                        blocks.append(f"{self._gradient(index, width)}█")
                    else:
                        blocks.append(f"{p.muted}░")
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


class ATXConsole:
    def __init__(self, settings: Settings, ui: ConsoleUI) -> None:
        self.settings = settings
        self.ui = ui

    def banner(self) -> None:
        p = self.ui.palette
        self.ui.clear()
        self.ui.line()
        logo = (
            (p.yellow, "  ██████╗ ██╗     ██╗  ██╗██╗   ██╗███╗   ███╗"),
            (p.orange, " ██╔════╝ ██║     ██║ ██╔╝██║   ██║████╗ ████║"),
            (p.pink, " ██║  ███╗██║     █████╔╝ ██║   ██║██╔████╔██║"),
            (p.magenta, " ██║   ██║██║     ██╔═██╗ ╚██╗ ██╔╝██║╚██╔╝██║"),
            (p.purple, " ╚██████╔╝███████╗██║  ██╗ ╚████╔╝ ██║ ╚═╝ ██║"),
            (p.cyan, "  ╚═════╝ ╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚═╝     ╚═╝"),
        )
        for color, text in logo:
            self.ui.line(f"{color}{text}{p.reset}")
        self.ui.line()
        self.ui.line(f"                    {p.muted}{p.italic}ATX remote power console{p.reset}")
        self.ui.line()
        self.ui.rule()
        self.ui.line(f"  {p.muted}host{p.reset}    {p.white}{p.bold}{self.settings.host}{p.reset}")
        if self.settings.target_label:
            self.ui.line(
                f"  {p.muted}target{p.reset}  "
                f"{p.white}{self.settings.target_label}{p.reset}"
            )
        self.ui.line(
            f"  {p.muted}{p.italic}"
            "This KVM presses the attached PC's power/reset wires."
            f"{p.reset}"
        )
        self.ui.line()

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
            )
        except OSError as exc:
            return CommandResult(126, str(exc))
        return CommandResult(completed.returncode, self._decode(completed.stdout))

    def read_state(self) -> str:
        if not self.board_present():
            return "NO-BOARD"
        result = self.invoke("power_state", combine_stderr=False)
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
        if self.confirm(f"Pulse power ON on the machine attached to {self.settings.host}?"):
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
        p = self.ui.palette
        self.ui.rule()
        self.ui.line(
            f"  {p.pink}{p.bold}‹1›{p.reset}  status           "
            f"{p.muted}read power_state (on / off / sleep){p.reset}"
        )
        self.ui.line(
            f"  {p.cyan}{p.bold}‹2›{p.reset}  power on         "
            f"{p.muted}graceful start if off{p.reset}"
        )
        self.ui.line(
            f"  {p.cyan}{p.bold}‹3›{p.reset}  power off        "
            f"{p.muted}ACPI / short press — preferred off{p.reset}"
        )
        self.ui.line(
            f"  {p.cyan}{p.bold}‹4›{p.reset}  power off HARD   "
            f"{p.muted}long press — last resort{p.reset}"
        )
        self.ui.line(
            f"  {p.cyan}{p.bold}‹5›{p.reset}  reset            "
            f"{p.muted}reset header — hard reboot{p.reset}"
        )
        self.ui.rule()
        self.ui.line(
            f"  {p.purple}{p.bold}‹6›{p.reset}  raw short click  "
            f"{p.muted}no on/off check{p.reset}"
        )
        self.ui.line(f"  {p.purple}{p.bold}‹7›{p.reset}  raw long click")
        self.ui.line(f"  {p.purple}{p.bold}‹8›{p.reset}  raw reset click")
        self.ui.line(
            f"  {p.pink}{p.bold}‹9›{p.reset}  board serial     "
            f"{p.muted}get_sn{p.reset}"
        )
        self.ui.line(f"  {p.muted}{p.bold}‹q›{p.reset}  quit")
        self.ui.rule()
        return self.ui.ask(f"  {p.magenta}{p.bold}❯{p.reset} {p.white}choose{p.reset} ")

    def pause(self) -> bool:
        p = self.ui.palette
        return self.ui.ask(f"  {p.muted}enter to return to menu{p.reset} ") is not None

    def goodbye(self, reason: str = "bye") -> None:
        p = self.ui.palette
        self.ui.line(f"  {p.muted}{reason}{p.reset}")
        self.ui.line()

    def run(self) -> int:
        actions = {
            "1": self.show_status,
            "2": self.do_on,
            "3": self.do_off,
            "4": self.do_off_hard,
            "5": self.do_reset,
            "6": self.do_click_short,
            "7": self.do_click_long,
            "8": self.do_click_reset,
            "9": self.do_serial,
        }
        while True:
            self.banner()
            if not self.need_board():
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
            if normalized in {"q", "0", "quit", "exit"}:
                self.goodbye()
                return 0
            action = actions.get(normalized)
            if action is None:
                self.ui.warn("not a menu item")
                self.ui.brief_delay()
                continue
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
