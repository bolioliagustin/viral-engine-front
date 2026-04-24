#!/usr/bin/env python3
"""
GitHub Webhook Auto-Deploy Server
Listens on port 9000 for GitHub push events to main branch.
Runs deploy.sh on every push.

Env vars:
  GITHUB_WEBHOOK_SECRET  — must match the secret set in GitHub repo settings
"""

import hmac
import hashlib
import json
import os
import subprocess
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [webhook] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "").encode()
DEPLOY_SCRIPT = os.path.join(os.path.dirname(__file__), "deploy.sh")
PORT = int(os.getenv("WEBHOOK_PORT", "9000"))


class WebhookHandler(BaseHTTPRequestHandler):

    def do_POST(self):
        if self.path != "/webhook":
            self._respond(404, b"not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        # ── Verify HMAC-SHA256 signature ──────────────────────────────────
        if SECRET:
            sig_header = self.headers.get("X-Hub-Signature-256", "")
            expected = "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig_header, expected):
                logging.warning("Invalid webhook signature — request rejected")
                self._respond(401, b"invalid signature")
                return

        # ── Only act on pushes to main ────────────────────────────────────
        event = self.headers.get("X-GitHub-Event", "")
        if event != "push":
            self._respond(200, b"ignored (not a push)")
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._respond(400, b"invalid json")
            return

        ref = payload.get("ref", "")
        if ref != "refs/heads/main":
            logging.info(f"Ignored push to {ref}")
            self._respond(200, b"ignored (not main)")
            return

        commit = payload.get("after", "unknown")[:7]
        pusher = payload.get("pusher", {}).get("name", "unknown")
        logging.info(f"Push to main by {pusher} ({commit}) — triggering deploy")

        # ── Run deploy script async (don't block the HTTP response) ──────
        subprocess.Popen(
            ["bash", DEPLOY_SCRIPT],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )

        self._respond(200, f"deploying {commit}".encode())

    def _respond(self, code: int, body: bytes):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # use our own logging


if __name__ == "__main__":
    if not SECRET:
        logging.warning("GITHUB_WEBHOOK_SECRET not set — signature verification disabled!")

    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    logging.info(f"Webhook server listening on port {PORT}")
    server.serve_forever()
