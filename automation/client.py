# SPDX-License-Identifier: GPL-3.0-or-later
"""Standard-library client for the local root-only automation socket."""
import http.client
import json
import socket


class LocalConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect("/run/kvmcontrol/automation.sock")


def request(path="/scheduler", body=None):
    connection = LocalConnection("localhost", timeout=45)
    try:
        connection.request("GET" if body is None else "POST", path,
                           body=None if body is None else json.dumps(body),
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        data = json.loads(response.read())
        if response.status != 200 or not data.get("ok"):
            raise RuntimeError(data.get("result", {}).get("error_msg", "Automation request failed"))
        return data["result"]
    finally:
        connection.close()


def main():
    import sys
    try:
        envelope = json.loads(sys.stdin.read(262144))
        path = envelope.get('path', '/scheduler')
        from automation.fleet import ALLOWED_PATHS
        if path not in ALLOWED_PATHS:
            raise ValueError('Unsupported fleet operation')
        result = request(path, envelope.get('body'))
        print(json.dumps({'ok': True, 'result': result}))
    except Exception as error:
        print(json.dumps({'ok': False, 'result': {'error_msg': str(error)}}))
        return 1
    return 0


if __name__ == '__main__':
    import sys
    # The file may be invoked directly over SSH, with no package installation.
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
