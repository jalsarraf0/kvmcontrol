#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run from a backup directory on the KVM to restore the pre-install files."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run rollback as root on the KVM')
    backup = Path(__file__).resolve().parent
    state = Path('/etc/kvmd/user/power-schedules.json')
    if state.exists() and json.loads(state.read_text()).get('armed'):
        raise SystemExit('Pause scheduling before rollback')
    init = Path('/etc/init.d/S99kvmcontrol')
    if init.exists():
        subprocess.run([str(init), 'stop'], check=False)
        pidfile = Path('/run/kvmcontrol/service.pid')
        for _ in range(20):
            try:
                os.kill(int(pidfile.read_text()), 0)
            except (OSError, ValueError):
                break
            time.sleep(0.25)
        else:
            raise SystemExit('Automation is still running; no files restored')
    for item in json.loads((backup / 'manifest.json').read_text()):
        destination = Path(item['path'])
        if item['backup']:
            shutil.copy2(item['backup'], destination)
        else:
            destination.unlink(missing_ok=True)
    subprocess.run(['/usr/sbin/nginx', '-t', '-p', '/etc/kvmd/nginx', '-c', '/etc/kvmd/nginx-kvmd.conf',
                    '-g', 'pid /run/kvmd/nginx.pid; user root; error_log stderr;'], check=True)
    os.kill(int(Path('/run/kvmd/nginx.pid').read_text()), signal.SIGHUP)
    if init.exists():
        subprocess.run([str(init), 'start'], check=True)
    print('Restored previous files; schedule data retained. No hardware actions sent.')


if __name__ == '__main__':
    main()
