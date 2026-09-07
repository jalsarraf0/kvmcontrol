# SPDX-License-Identifier: GPL-3.0-or-later
"""Animated, line-buffered terminal chrome. No raw mode or synthetic input."""
import json
import os
from pathlib import Path
import re
import shutil
import sys
import time

CONFIG = Path(os.environ.get('ATX_UI_CONFIG', '/etc/kvmd/user/kvmcontrol-ui.json'))
THEMES = {'synthwave': ((5,217,232), (242,34,255)), 'aurora': ((117,243,196), (105,153,255)),
          'ember': ((255,181,97), (255,77,109)), 'ice': ((147,219,255), (185,180,255))}
ANSI = re.compile(r'\x1b\[[0-9;]*m')


def load():
    try:
        value = json.loads(CONFIG.read_text())
        if not isinstance(value, dict):
            value = {}
    except (OSError, ValueError):
        value = {}
    return {'theme': value.get('theme', 'synthwave') if value.get('theme', 'synthwave') in THEMES else 'synthwave',
            'compact': value.get('compact') is True, 'animations': value.get('animations', True) is True}


def save(value):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(CONFIG, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(value, file, indent=2)


def clean(value):
    return ''.join(char for char in str(value) if char.isprintable())


def width():
    return max(24, min(100, shutil.get_terminal_size((88, 24)).columns - 4))


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
    if not ui.animate or not ui.palette.enabled or not getattr(ui.input, 'isatty', lambda: False)():
        return
    size = min(42, width()-8)
    p = ui.palette
    # Cosmetic entrance is capped at 0.45s; it never delays a hardware command.
    try:
        ui.write('\x1b[?25l')
        for frame in range(12):
            beam = frame * (size-5) // 11
            line = '░' * beam + '▓██▓' + '░' * max(0, size-beam-4)
            ui.write('\r  ' + p.cyan + line + p.reset + '  COMMAND DECK', flush=True)
            time.sleep(0.035)
    finally:
        ui.write('\r\x1b[2K\x1b[?25h', flush=True)


def header(console, snapshot=None):
    ui, p = console.ui, console.ui.palette
    ui.clear()
    ui.line()
    if not ui.compact and width() >= 68:
        for index, line in enumerate((
            ' ██████╗ ██╗     ██╗  ██╗██╗   ██╗███╗   ███╗',
            '██╔════╝ ██║     ██║ ██╔╝██║   ██║████╗ ████║',
            '██║  ███╗██║     █████╔╝ ██║   ██║██╔████╔██║',
            '██║   ██║██║     ██╔═██╗ ╚██╗ ██╔╝██║╚██╔╝██║',
            '╚██████╔╝███████╗██║  ██╗ ╚████╔╝ ██║ ╚═╝ ██║',
            ' ╚═════╝ ╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚═╝     ╚═╝')):
            ui.line('  ' + (p.magenta if index < 3 else p.cyan) + line + p.reset)
    else:
        ui.line('  ' + p.bold + p.cyan + 'GLKVM / COMMAND DECK' + p.reset)
    ui.line('  ' + p.muted + 'POWER  /  FLEET  /  AUTOMATION  /  RECOVERY' + p.reset)
    ui.line()
    selected = getattr(console, 'target_label', console.settings.host)
    state = snapshot or {}
    active = state.get('active')
    lines = ['TARGET  ' + clean(selected) + '    •    ' + time.strftime('%H:%M:%S %Z'),
             'ENGINE  ' + ('LOCKED' if state.get('error') else 'ARMED' if state.get('armed') else 'PAUSED' if state else 'UNAVAILABLE') +
             '    /    QUEUE  ' + str(len(state.get('jobs', []))) +
             '    /    THEME  ' + ui.theme.upper()]
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
    ui, p = console.ui, console.ui.palette
    size = width()
    section = None
    for key, label, hint, group, badge in items + extras:
        if section != group:
            section = group
            ui.line()
            ui.line('  ' + p.magenta + p.bold + group + p.reset)
        badge_text = '[' + badge + ']' if badge else ''
        if ui.compact or size < 70:
            text = f'[{key}] {label}'
            padding = max(1, size - len(text) - len(badge_text))
            ui.line('  ' + p.cyan + f'[{key}] ' + p.reset + label + ' '*padding + p.muted + badge_text + p.reset)
        else:
            left = f'[{key}] {label:<19} {hint}'
            max_left = size - len(badge_text) - 2
            left = left[:max_left]
            ui.line('  ' + p.cyan + left[:4] + p.reset + left[4:] + ' '*max(2, size-len(left)-len(badge_text)) +
                    (p.red if badge in ('DANGER','HARD','RAW-HOLD') else p.muted) + badge_text + p.reset)
    ui.line()
    ui.rule(min(size, 90))
    ui.line('  ' + p.muted + '[/] search   [t] appearance   [q] quit   •   No raw keyboard mode' + p.reset)
    return ui.ask('  ' + p.magenta + '❯ ' + p.reset + 'command › ')
