# SPDX-License-Identifier: GPL-3.0-or-later
"""Animated terminal chrome plus the exact appliance MOTD logo."""
import json
import os
from pathlib import Path
import re
import shutil
import time

from automation import tui_nav

CONFIG = Path(os.environ.get('ATX_UI_CONFIG', '/etc/kvmd/user/kvmcontrol-ui.json'))
THEMES = {'synthwave': ((5,217,232), (242,34,255)), 'aurora': ((117,243,196), (105,153,255)),
          'ember': ((255,181,97), (255,77,109)), 'ice': ((147,219,255), (185,180,255))}
ANSI = re.compile(r'\x1b\[[0-9;]*m')
QUICK_POWER = {'1', '2', '3'}
QUICK_EXTRA = {'f', 'r', 'c', 'v', 'l', 'h', 't'}

# Exact /etc/motd from GL.iNet Comet (identical on dominus and amarillokvm).
MOTD = (
    "\n"
    "  ██████╗ ██╗     ██╗  ██╗██╗   ██╗███╗   ███╗\n"
    " ██╔════╝ ██║     ██║ ██╔╝██║   ██║████╗ ████║\n"
    " ██║  ███╗██║     █████╔╝ ██║   ██║██╔████╔██║\n"
    " ██║   ██║██║     ██╔═██╗ ╚██╗ ██╔╝██║╚██╔╝██║\n"
    " ╚██████╔╝███████╗██║  ██╗ ╚████╔╝ ██║ ╚═╝ ██║\n"
    "  ╚═════╝ ╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚═╝     ╚═╝\n"
    "\n"
    " ─────────────────────────────────────────────\n"
    "   Welcome to Glkvm\n"
    "   System is ready. Have a productive day!\n"
    " ─────────────────────────────────────────────\n"
    "\n"
)


def load():
    try:
        value = json.loads(CONFIG.read_text())
        if not isinstance(value, dict):
            value = {}
    except (OSError, ValueError):
        value = {}
    theme = value.get('theme', 'synthwave')
    return {
        'theme': theme if theme in THEMES else 'synthwave',
        'compact': value.get('compact') is True,
        'animations': value.get('animations', True) is True,
        'quick': value.get('quick') is True,
    }


def save(value):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CONFIG, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(value, file, indent=2)


def clean(value):
    return ''.join(char for char in str(value) if char.isprintable())


def width():
    return max(24, min(100, shutil.get_terminal_size((88, 24)).columns - 4))


def motd_text():
    configured = os.environ.get('ATX_MOTD', '/etc/motd')
    path = Path(configured) if configured else Path('/etc/motd')
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return MOTD
    if '██████╗' in text and 'Glkvm' in text:
        return text if text.endswith('\n') else text + '\n'
    return MOTD


def box(ui, lines, title=''):
    p = ui.palette
    size = width()
    title = clean(title)[:size-6]
    ui.line('  ' + p.cyan + '╭─ ' + title + ' ' + '─' * max(0, size - len(title) - 5) + '╮' + p.reset)
    for line in lines:
        text = clean(line)[:size-4]
        ui.line('  ' + p.cyan + '│ ' + p.reset + text.ljust(size-4) + p.cyan + ' │' + p.reset)
    ui.line('  ' + p.cyan + '╰' + '─' * (size-2) + '╯' + p.reset)


def entrance(ui):
    if ui.quick or not ui.animate or not ui.palette.enabled or not getattr(ui.input, 'isatty', lambda: False)():
        return
    size = min(42, width()-8)
    p = ui.palette
    try:
        ui.write('\x1b[?25l')
        for frame in range(12):
            beam = frame * (size-5) // 11
            line = '░' * beam + '▓██▓' + '░' * max(0, size-beam-4)
            ui.write('\r  ' + p.cyan + line + p.reset + '  COMMAND DECK', flush=True)
            time.sleep(0.035)
    finally:
        ui.write('\r\x1b[2K\x1b[?25h', flush=True)


def _paint_motd(ui):
    p = ui.palette
    lines = motd_text().splitlines()
    art_index = 0
    for line in lines:
        if '█' in line or '╗' in line or '╔' in line or '╚' in line or '═' in line and '██' in line:
            color = p.magenta if art_index < 3 else p.cyan
            ui.line(color + line + p.reset)
            art_index += 1
        elif line.strip().startswith('─'):
            ui.line(p.muted + line + p.reset)
        elif 'Welcome' in line:
            ui.line(p.bold + p.white + line + p.reset)
        elif line.strip():
            ui.line(p.muted + line + p.reset)
        else:
            ui.line()


def header(console, snapshot=None):
    ui, p = console.ui, console.ui.palette
    ui.clear()
    if width() >= 48 and not (ui.quick and ui.compact):
        _paint_motd(ui)
    else:
        ui.line()
        ui.line('  ' + p.bold + p.cyan + 'GLKVM' + p.reset + p.muted + '  ·  Welcome to Glkvm' + p.reset)
        ui.line()
    selected = getattr(console, 'target_label', console.settings.host)
    state = snapshot or {}
    active = state.get('active')
    mode = 'QUICK' if ui.quick else 'FULL'
    lines = ['TARGET  ' + clean(selected) + '    •    ' + time.strftime('%H:%M:%S %Z'),
             'ENGINE  ' + ('LOCKED' if state.get('error') else 'ARMED' if state.get('armed') else 'PAUSED' if state else 'UNAVAILABLE') +
             '    /    QUEUE  ' + str(len(state.get('jobs', []))) +
             '    /    ' + mode + '  ' + ui.theme.upper()]
    if active:
        lines.append('WORKFLOW  ' + clean(active.get('phase', 'running')) + '   [c] cancel')
    next_jobs = sorted((job for job in state.get('jobs', []) if job['enabled']), key=lambda job: job['at'])
    if next_jobs and state.get('armed'):
        from automation.calendar_rules import stamp
        job = next_jobs[0]
        seconds = max(0, int(stamp(job['at']) - time.time()))
        lines.append(('UPCOMING  ' if seconds <= state.get('settings', {}).get('warning_seconds', 300) else 'NEXT  ') +
                     clean(job['name']) + f'  in {seconds//3600}h {seconds%3600//60}m {seconds%60}s  [l] snooze/skip')
    box(ui, lines, 'MISSION STATUS')


def menu(console, items, extras):
    if console.ui.quick:
        items = [row for row in items if row[0] in QUICK_POWER]
        extras = [row for row in extras if row[0] in QUICK_EXTRA]
    rows = list(items) + list(extras)
    key, console.menu_index = tui_nav.pick(console, rows, getattr(console, 'menu_index', 0))
    return key
