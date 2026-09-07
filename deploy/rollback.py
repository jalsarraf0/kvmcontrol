#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run from a backup directory on the KVM to restore the pre-install files."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def convert_to_v1(backup, current):
    helper = backup / 'store_compat.py'
    if not helper.exists():
        raise SystemExit('v2 schedule snapshot kept; missing store_compat.py in this backup')
    spec = importlib.util.spec_from_file_location('kvmcontrol_store_compat', helper)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.to_v1(current)


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run rollback as root on the KVM')
    backup = Path(__file__).resolve().parent
    state = Path('/etc/kvmd/user/power-schedules.json')
    if state.exists() and json.loads(state.read_text()).get('armed'):
        raise SystemExit('Pause scheduling before rollback')
    init = Path('/etc/init.d/S99kvmcontrol')
    pidfile = Path('/run/kvmcontrol/service.pid')
    socket_path = Path('/run/kvmcontrol/automation.sock')
    if init.exists():
        subprocess.run([str(init), 'stop'], check=False)
        for _ in range(30):
            try:
                os.kill(int(pidfile.read_text()), 0)
            except (OSError, ValueError):
                break
            time.sleep(0.25)
        else:
            raise SystemExit('Automation is still running; no files restored')
        socket_path.unlink(missing_ok=True)
    for item in json.loads((backup / 'manifest.json').read_text()):
        destination = Path(item['path'])
        if item['backup']:
            shutil.copy2(item['backup'], destination)
        else:
            destination.unlink(missing_ok=True)
    calendar = Path('/usr/share/kvmcontrol/automation/calendar_rules.py')
    if state.exists() and not calendar.exists():
        current = json.loads(state.read_text())
        if current.get('version') == 2:
            shutil.copy2(state, state.with_name('power-schedules.json.v2'))
            state.write_text(json.dumps(convert_to_v1(backup, current), indent=2))
            os.chmod(state, 0o600)
    subprocess.run(['/usr/sbin/nginx', '-t', '-p', '/etc/kvmd/nginx', '-c', '/etc/kvmd/nginx-kvmd.conf',
                    '-g', 'pid /run/kvmd/nginx.pid; user root; error_log stderr;'], check=True)
    os.kill(int(Path('/run/kvmd/nginx.pid').read_text()), signal.SIGHUP)
    if init.exists():
        subprocess.run([str(init), 'start'], check=True)
    print('Restored previous files; schedule data retained or converted. No hardware actions sent.')


if __name__ == '__main__':
    main()
