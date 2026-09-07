#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Install an explicit file manifest on a GL.iNet Comet, with rollback.

No feature endpoints, hardware commands, network scans, or test suite are run.
Only Python parsing, nginx configuration validation and process checks run.
"""
import ast
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

ROOT = Path('/usr/share/kvmcontrol')
EXTRA = Path('/usr/share/kvmd/extras/kvmcontrol/nginx.ctx-server.conf')
INIT = Path('/etc/init.d/S99kvmcontrol')
SOCKET = Path('/run/kvmcontrol/automation.sock')
PID = Path('/run/kvmcontrol/service.pid')


def atomic_write(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.kvmcontrol-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_fleet_document(data):
    import re
    if not isinstance(data, list) or not 1 <= len(data) <= 8:
        raise ValueError('Fleet profiles must contain 1–8 targets')
    ids = set()
    for target in data:
        if not isinstance(target, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}', str(target.get('id', ''))) or target['id'] in ids:
            raise ValueError('Invalid or duplicate target ID')
        ids.add(target['id'])
        host = target.get('host', '')
        if not isinstance(host, str) or host and not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.@-]{0,100}', host):
            raise ValueError('Invalid SSH target')
        label = target.get('label', '')
        if not isinstance(label, str) or not 1 <= len(label) <= 60 or not label.isprintable():
            raise ValueError('Invalid target label')
        url = target.get('url', '')
        if not isinstance(url, str) or not (url.startswith('https://') or url == '/command/'):
            raise ValueError('Fleet dashboard URL must use HTTPS')


def nginx_check():
    subprocess.run(['/usr/sbin/nginx', '-t', '-p', '/etc/kvmd/nginx',
                    '-c', '/etc/kvmd/nginx-kvmd.conf',
                    '-g', 'pid /run/kvmd/nginx.pid; user root; error_log stderr;'], check=True)


def nginx_reload():
    os.kill(int(Path('/run/kvmd/nginx.pid').read_text()), signal.SIGHUP)


def pid_alive():
    try:
        os.kill(int(PID.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def stop_service():
    if INIT.exists():
        subprocess.run([str(INIT), 'stop'], check=False)
    for _ in range(30):
        if not pid_alive() and not SOCKET.exists():
            break
        time.sleep(0.5)
    else:
        if pid_alive():
            os.kill(int(PID.read_text()), signal.SIGTERM)
            time.sleep(1)
        if pid_alive():
            raise RuntimeError('Automation service did not stop; no files will be replaced')
    if SOCKET.exists():
        SOCKET.unlink()
    if PID.exists() and not pid_alive():
        PID.unlink()


def main():
    if os.geteuid() != 0:
        raise SystemExit('Install as root on the KVM')
    source = Path(__file__).resolve().parent.parent
    if not Path('/usr/lib/python3.12/site-packages/kvmd').is_dir():
        raise SystemExit('This installer targets Comet firmware with Python 3.12 KVMD')
    if not Path('/etc/kvmd/nginx/gl.ctx-server.conf').is_file():
        raise SystemExit('GL.iNet nginx layout not found')
    if not Path('/etc/kvmd/user').is_dir():
        raise SystemExit('Persistent KVMD user storage is missing')
    state_path = Path('/etc/kvmd/user/power-schedules.json')
    if state_path.exists() and json.loads(state_path.read_text()).get('armed'):
        raise SystemExit('Pause scheduling before updating the installed automation service')
    replacements = {}
    for filename in ('atx_console.py', 'atx-console.sh'):
        content = (source / filename).read_bytes()
        replacements[ROOT / filename] = (content, 0o755)
        # Preserve the documented existing SSH entry points, using a launcher.
        interpreter = 'python3' if filename.endswith('.py') else 'bash'
        if filename.endswith('.py'):
            wrapper = '#!/usr/bin/env python3\nimport os\nimport sys\nos.execv("/usr/bin/python3", ["python3", "/usr/share/kvmcontrol/atx_console.py", *sys.argv[1:]])\n'
        else:
            wrapper = '#!/bin/sh\nexec ' + interpreter + ' /usr/share/kvmcontrol/' + filename + ' "$@"\n'
        for entry in Path('/home').glob('*/scripts/' + filename):
            replacements[entry] = (wrapper.encode(), 0o755)
    for path in (source / 'automation').glob('*.py'):
        replacements[ROOT / 'automation' / path.name] = (path.read_bytes(), 0o644)
    replacements.update({
        ROOT / 'web/command/index.html': ((source / 'web/command/index.html').read_bytes(), 0o644),
        ROOT / 'web/static/command.css': ((source / 'web/share/css/kvm/command.css').read_bytes(), 0o644),
        ROOT / 'web/static/command.js': ((source / 'web/share/js/kvm/command.js').read_bytes(), 0o644),
        ROOT / 'LICENSE': ((source / 'LICENSE').read_bytes(), 0o644),
        EXTRA: ((source / 'deploy/nginx-command.conf').read_bytes(), 0o644),
        INIT: ((source / 'deploy/S99kvmcontrol').read_bytes(), 0o755),
    })
    # An additive link in the vendor shell leaves bundled application code intact.
    vendor_index = Path('/usr/share/kvmd/glweb/index.html')
    if vendor_index.is_file():
        html = vendor_index.read_text()
        if 'id="kvmcontrol-link"' not in html:
            if '</body>' not in html:
                raise RuntimeError('Vendor index has no body boundary; refusing to guess')
            link = '<a id="kvmcontrol-link" href="/command/" style="position:fixed;right:16px;bottom:16px;z-index:10000;background:#75f3c4;color:#08261d;padding:9px 14px;border-radius:8px;font:600 12px system-ui;text-decoration:none">◈ Command Center</a>'
            html = html.replace('</body>', link + '</body>')
        replacements[vendor_index] = (html.encode(), 0o644)
        # Keep any precompressed shell consistent with nginx gzip_static.
        import gzip
        replacements[vendor_index.with_suffix('.html.gz')] = (gzip.compress(html.encode(), mtime=0), 0o644)
    for destination, (content, _) in replacements.items():
        if destination.suffix == '.py':
            ast.parse(content, filename=str(destination))
    # Verify existing nginx configuration before modifying its include tree.
    nginx_check()
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup = Path('/etc/kvmd/user/kvmcontrol-backups') / stamp
    backup.mkdir(parents=True, mode=0o700)
    manifest = []
    for index, destination in enumerate(replacements):
        original = backup / str(index)
        exists = destination.exists()
        if exists:
            shutil.copy2(destination, original)
        manifest.append({'path': str(destination), 'backup': str(original) if exists else None})
    (backup / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    shutil.copy2(source / 'deploy/rollback.py', backup / 'rollback.py')
    shutil.copy2(source / 'automation/store_compat.py', backup / 'store_compat.py')
    if state_path.exists():
        shutil.copy2(state_path, backup / 'power-schedules.json')
    fleet_path = Path('/etc/kvmd/user/kvmcontrol-fleet.json')
    if fleet_path.exists():
        shutil.copy2(fleet_path, backup / 'kvmcontrol-fleet.json')
    previous_service = INIT.exists()
    stop_service()
    try:
        for destination, (content, mode) in replacements.items():
            atomic_write(destination, content, mode)
        nginx_check()
        fleet_src = source / 'local' / 'fleet.json'
        if fleet_src.is_file() and not fleet_path.exists():
            validate_fleet_document(json.loads(fleet_src.read_text()))
            atomic_write(fleet_path, fleet_src.read_bytes(), 0o600)
        subprocess.run([str(INIT), 'start'], check=True)
        for _ in range(40):
            if SOCKET.exists() and pid_alive() and stat.S_ISSOCK(SOCKET.stat().st_mode):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('Automation service did not create its Unix socket')
        nginx_reload()
        hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in replacements}
        (backup / 'installed-sha256.json').write_text(json.dumps(hashes, indent=2))
        print('Installed command center and automation service. No feature tests run.')
        print('Backup and rollback: ' + str(backup))
    except Exception:
        stop_service()
        for item in manifest:
            path = Path(item['path'])
            if item['backup']:
                original = Path(item['backup'])
                atomic_write(path, original.read_bytes(), original.stat().st_mode & 0o777)
            else:
                path.unlink(missing_ok=True)
        nginx_check()
        nginx_reload()
        if previous_service:
            subprocess.run([str(INIT), 'start'], check=False)
        raise


if __name__ == '__main__':
    main()
