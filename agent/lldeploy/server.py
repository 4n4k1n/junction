"""Local HTTP API of the deploy agent. Bound to 127.0.0.1 only.

GET  /device                          toolchain and board status
POST /compile  {build_id, sketch}     validate + compile, returns diagnostics
POST /flash    {build_id, approval_token, expected_events?}   upload; needs a valid approval token
GET  /logs?build_id=&since=&capture_s=   status events and runtime log

Requests must be addressed to localhost (blocks DNS rebinding), must not come from a web page
(no Origin header), and POSTs need X-Agent-Token when LEARNLENS_AGENT_TOKEN is set.
"""
from __future__ import annotations

import hmac
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .core import DeployAgent, Rejected

HOST = "127.0.0.1"  # not configurable: the agent is never exposed to the network
MAX_BODY = 64 * 1024
STATUS_FOR_STAGE = {"validate": 422, "compile": 409, "approval": 403, "device": 409, "upload": 502}


def make_handler(agent: DeployAgent):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LearnLensAgent"

        def log_message(self, fmt, *args):  # quiet: status goes to the event log
            pass

        def _send(self, status: int, payload: dict) -> None:
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _allowed(self, write: bool) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost") or self.headers.get("Origin"):
                self._send(403, {"error": "local requests only"})
                return False
            token = os.getenv("LEARNLENS_AGENT_TOKEN")
            if write and token and not hmac.compare_digest(self.headers.get("X-Agent-Token") or "", token):
                self._send(401, {"error": "bad agent token"})
                return False
            return True

        def _body(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if not 0 < length <= MAX_BODY:
                self._send(413 if length > MAX_BODY else 400, {"error": "JSON body required (max 64 KB)"})
                return None
            try:
                body = json.loads(self.rfile.read(length))
            except ValueError:
                body = None
            if not isinstance(body, dict):
                self._send(400, {"error": "body must be a JSON object"})
                return None
            return body

        def _run(self, action) -> None:
            try:
                self._send(200, action())
            except Rejected as e:
                self._send(STATUS_FOR_STAGE.get(e.stage, 400), {"error": str(e), "stage": e.stage})

        def do_GET(self):
            url = urlparse(self.path)
            if not self._allowed(write=False):
                return
            if url.path == "/device":
                self._send(200, agent.device())
            elif url.path == "/logs":
                q = parse_qs(url.query)
                try:
                    since, capture = int(q.get("since", ["0"])[0]), float(q.get("capture_s", ["0"])[0])
                except ValueError:
                    return self._send(400, {"error": "since and capture_s must be numbers"})
                self._run(lambda: agent.logs(q.get("build_id", [""])[0], since, capture))
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._allowed(write=True):
                return
            if self.path not in ("/compile", "/flash"):
                return self._send(404, {"error": "not found"})
            body = self._body()
            if body is None:
                return
            if self.path == "/compile":
                self._run(lambda: agent.compile(body))
            else:
                events = body.get("expected_events")
                events = [e for e in events if isinstance(e, str)][:20] if isinstance(events, list) else []
                self._run(lambda: agent.flash(body.get("build_id"), body.get("approval_token") or "", events))

    return Handler


def make_server(agent: DeployAgent, port: int) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), make_handler(agent))
