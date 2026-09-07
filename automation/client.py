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
    connection = LocalConnection("localhost", timeout=35)
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
