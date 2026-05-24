#!/usr/bin/env python3
"""
AmneziaWG health sidecar.
Listens on 0.0.0.0:8080, returns 200/{"status":"ok"} if awg0 is active,
503/{"status":"down"} otherwise. Controller polls this to decide DNS.
"""

import subprocess
import json
import http.server
import sys

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080


def awg0_healthy() -> tuple[bool, dict]:
    active = subprocess.run(
        ["systemctl", "is-active", "awg-quick@awg0"],
        capture_output=True, text=True
    ).stdout.strip() == "active"

    iface = subprocess.run(
        ["ip", "link", "show", "awg0"],
        capture_output=True, text=True
    )
    iface_up = "state UNKNOWN" in iface.stdout or "UP" in iface.stdout

    healthy = active and iface_up
    return healthy, {"status": "ok" if healthy else "down",
                     "awg0_active": active, "interface_up": iface_up}


class HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        healthy, body = awg0_healthy()
        data = json.dumps(body).encode()
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        pass  # suppress per-request logs; controller logs state changes


if __name__ == "__main__":
    server = http.server.HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"awg-health sidecar listening on port {PORT}", flush=True)
    server.serve_forever()
