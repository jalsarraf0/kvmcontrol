# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit host profiles. Requests travel over existing SSH trust, never new keys."""
import concurrent.futures
import json
from pathlib import Path
import re
import socket
import subprocess

from automation.client import request

ALLOWED_PATHS = {'/scheduler', '/scheduler/status', '/scheduler/jobs', '/scheduler/job', '/scheduler/armed',
                 '/scheduler/run', '/scheduler/cancel', '/scheduler/presets', '/scheduler/settings',
                 '/scheduler/backup', '/scheduler/restore'}
PROFILE_PATH = Path('/etc/kvmd/user/kvmcontrol-fleet.json')


def profiles():
    try:
        data = json.loads(PROFILE_PATH.read_text())
    except FileNotFoundError:
        return [{'id': 'local', 'label': socket.gethostname(), 'host': '', 'url': '/command/'}]
    if not isinstance(data, list) or not 1 <= len(data) <= 8:
        raise ValueError('Fleet profiles must contain 1–8 targets')
    ids = set()
    for target in data:
        if not isinstance(target, dict) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,32}', target.get('id', '')) or target['id'] in ids:
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
    return data


def call(target_id, path='/scheduler', body=None):
    if path not in ALLOWED_PATHS:
        raise ValueError('Unsupported fleet operation')
    target = next((item for item in profiles() if item['id'] == target_id), None)
    if target is None:
        raise ValueError('Unknown target; refresh fleet profiles')
    if not target['host']:
        return request(path, body)
    args = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', target['host'],
            'python3 /usr/share/kvmcontrol/automation/client.py --rpc']
    result = subprocess.run(args, input=json.dumps({'path': path, 'body': body}),
                            capture_output=True, text=True, timeout=40)
    try:
        data = json.loads(result.stdout)
    except ValueError as error:
        raise RuntimeError('SSH target unavailable or not trusted; check existing SSH configuration') from error
    if result.returncode or not data.get('ok'):
        raise RuntimeError(data.get('result', {}).get('error_msg', 'Remote request failed'))
    return data['result']


def overview():
    def read(target):
        try:
            return {**target, 'state': call(target['id'], '/scheduler/status'), 'error': ''}
        except Exception as error:
            return {**target, 'state': None, 'error': str(error)}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(read, profiles()))
