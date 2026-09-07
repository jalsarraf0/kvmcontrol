# SPDX-License-Identifier: GPL-3.0-or-later
"""TTY arrow-key navigation. Falls back cleanly when stdin/stdout are not a pty."""
import os
import select
import shutil
import termios
import tty

SEQUENCES = {
    "\x1b[A": "up",
    "\x1b[B": "down",
    "\x1b[C": "right",
    "\x1b[D": "left",
    "\x1b[H": "home",
    "\x1b[F": "end",
    "\x1b[1~": "home",
    "\x1b[4~": "end",
    "\x1b[5~": "pageup",
    "\x1b[6~": "pagedown",
    "\x1b[Z": "btab",
    "\x1bOA": "up",
    "\x1bOB": "down",
    "\x1bOC": "right",
    "\x1bOD": "left",
}


def interactive(ui) -> bool:
    stdin_tty = bool(getattr(ui.input, "isatty", lambda: False)())
    stdout_tty = bool(ui.is_tty)
    term = os.environ.get("TERM", "")
    return (
        stdin_tty
        and stdout_tty
        and term.lower() != "dumb"
        and os.environ.get("ATX_LINE_MENU") != "1"
    )


def decode_sequence(buffer: str) -> str | None:
    if buffer in SEQUENCES:
        return SEQUENCES[buffer]
    if buffer == "\x1b":
        return "esc"
    if len(buffer) >= 6 or (len(buffer) >= 3 and not buffer.startswith("\x1b[")):
        return "esc"
    return None


class RawTerminal:
    def __init__(self, ui):
        self.ui = ui
        self.fd = None
        self.old = None

    def __enter__(self):
        stream = self.ui.input
        if not interactive(self.ui) or not hasattr(stream, "fileno"):
            return self
        self.fd = stream.fileno()
        self.old = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        self.ui.write("\x1b[?25l", flush=True)
        return self

    def __exit__(self, *_exc):
        if self.old is not None and self.fd is not None:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old)
        try:
            self.ui.write("\x1b[?25h", flush=True)
        except BrokenPipeError:
            pass


def _read_byte(stream, timeout):
    fd = stream.fileno()
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return ""
    data = os.read(fd, 1)
    return data.decode("latin1") if data else ""


def read_key(ui, timeout=None) -> str | None:
    stream = ui.input
    first = _read_byte(stream, timeout if timeout is not None else None)
    if first == "":
        if timeout is not None:
            return None
        return None
    if first == "\r" or first == "\n":
        return "enter"
    if first == "\t":
        return "tab"
    if first in ("\x7f", "\x08"):
        return "backspace"
    if first == "\x03":
        raise KeyboardInterrupt
    if first != "\x1b":
        return first
    buffer = first
    while True:
        nxt = _read_byte(stream, 0.05)
        if nxt == "":
            return decode_sequence(buffer) or "esc"
        buffer += nxt
        decoded = decode_sequence(buffer)
        if decoded and buffer in SEQUENCES:
            return decoded
        if len(buffer) >= 6:
            return "esc"


def _hotkeys(rows):
    return {key: index for index, (key, *_rest) in enumerate(rows)}


def _sections(rows):
    starts = []
    seen = None
    for index, row in enumerate(rows):
        group = row[3]
        if group != seen:
            starts.append(index)
            seen = group
    return starts


def draw_rows(ui, rows, selected):
    p = ui.palette
    size = max(40, min(100, shutil.get_terminal_size((88, 24)).columns - 4))
    section = None
    for index, (key, label, hint, group, badge) in enumerate(rows):
        if section != group:
            section = group
            ui.line()
            ui.line("  " + p.magenta + p.bold + group + p.reset)
        badge_text = "[" + badge + "]" if badge else ""
        cursor = "▸" if index == selected else " "
        if ui.quick or ui.compact or size < 70:
            body = f"{cursor} [{key}] {label}"
        else:
            body = f"{cursor} [{key}] {label:<19} {hint}"
        body = body[: max(8, size - len(badge_text) - 2)]
        color = p.bold + p.cyan if index == selected else p.cyan
        danger = p.red if badge in ("DANGER", "HARD", "RAW-HOLD") else p.muted
        pad = max(1, size - len(body) - len(badge_text))
        ui.line("  " + color + body + p.reset + " " * pad + danger + badge_text + p.reset)
    ui.line()
    ui.rule(min(size, 90))
    if not interactive(ui):
        hint = "[key] then enter   q quit   •   ssh -t for arrow keys"
    elif ui.quick:
        hint = "↑↓ move   enter select   tab full menu   letters still work   q quit"
    else:
        hint = "↑↓ move   ←→ section   enter select   tab quick   / search   q quit"
    ui.line("  " + p.muted + hint + p.reset)


def pick(console, rows, selected=0):
    ui = console.ui
    if not rows:
        return None, 0
    selected = max(0, min(selected, len(rows) - 1))
    starts = _sections(rows)
    keys = _hotkeys(rows)
    if not interactive(ui):
        draw_rows(ui, rows, selected)
        answer = ui.ask("  " + ui.palette.magenta + "❯ " + ui.palette.reset + "command › ")
        return (None if answer is None else answer.strip(), selected)
    try:
        with RawTerminal(ui):
            while True:
                console.banner(fetch=False)
                if not console.remote_target and not console.board_present():
                    ui.warn("ATX board not enumerated on this KVM yet")
                draw_rows(ui, rows, selected)
                key = read_key(ui)
                if key is None:
                    return None, selected
                if key == "up":
                    selected = (selected - 1) % len(rows)
                elif key == "down":
                    selected = (selected + 1) % len(rows)
                elif key == "left" and starts:
                    earlier = [start for start in starts if start < selected]
                    selected = earlier[-1] if earlier else starts[-1]
                elif key == "right" and starts:
                    later = [start for start in starts if start > selected]
                    selected = later[0] if later else starts[0]
                elif key == "home":
                    selected = 0
                elif key == "end":
                    selected = len(rows) - 1
                elif key == "pageup":
                    selected = max(0, selected - 8)
                elif key == "pagedown":
                    selected = min(len(rows) - 1, selected + 8)
                elif key in ("enter", " "):
                    return rows[selected][0], selected
                elif key in ("esc", "q"):
                    return "q", selected
                elif key == "tab":
                    return "tab", selected
                elif key in keys:
                    return key, keys[key]
                elif len(key) == 1:
                    return key, selected
    except (termios.error, OSError, ValueError):
        draw_rows(ui, rows, selected)
        answer = ui.ask("  " + ui.palette.magenta + "❯ " + ui.palette.reset + "command › ")
        return (None if answer is None else answer.strip(), selected)


def confirm(ui, prompt, default=False) -> bool:
    p = ui.palette
    ui.line(f"  {p.yellow}{prompt}{p.reset}")
    if not interactive(ui):
        answer = ui.ask(f"  {p.muted}[y/N]{p.reset} ")
        if answer is None:
            ui.line(f"  {p.muted}cancelled (input closed){p.reset}")
            return False
        return answer in {"y", "Y", "yes", "YES"}
    choice = default
    with RawTerminal(ui):
        while True:
            no = "[ NO ]" if not choice else "  no  "
            yes = "[ YES ]" if choice else "  yes "
            ui.write(
                "\r  "
                + (p.red + p.bold if not choice else p.muted)
                + no
                + p.reset
                + "     "
                + (p.green + p.bold if choice else p.muted)
                + yes
                + p.reset
                + "     "
                + p.muted
                + "←→  enter  y/n"
                + p.reset
                + "   ",
                flush=True,
            )
            key = read_key(ui)
            if key in ("left", "h"):
                choice = False
            elif key in ("right", "l"):
                choice = True
            elif key in ("y", "Y"):
                choice = True
                break
            elif key in ("n", "N", "esc"):
                choice = False
                break
            elif key == "enter":
                break
            elif key is None:
                choice = False
                break
    ui.line()
    if not choice:
        ui.line(f"  {p.muted}cancelled{p.reset}")
    return choice


def wait_for_key(ui) -> bool:
    p = ui.palette
    if not interactive(ui):
        return ui.ask(f"  {p.muted}enter to return to menu{p.reset} ") is not None
    ui.line(f"  {p.muted}any key returns to the menu{p.reset}")
    with RawTerminal(ui):
        return read_key(ui) is not None
