# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""HTTP API: authenticated create-sandbox endpoint streaming NDJSON events."""

import hmac
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config
from .sandbox import SandboxRunner
from .validation import ValidationError, validate_request

MAX_BODY = 64 * 1024 * 1024


class _APIHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        # Never log request bodies or authorization headers.
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        if self.path == "/healthz":
            self._json(200, {"status": "ok"})
        else:
            self._json_error(404, "not found")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        if self.path != "/v1/sandboxes":
            self._json_error(404, "not found")
            return

        app = self.server.app

        # Authenticate before touching the body so a bad token cannot make us
        # read (or wait for) a large declared payload.
        expected = f"Bearer {app.config.token}"
        provided = self.headers.get("Authorization", "")
        if not _authorized(provided, expected):
            self._json_error(401, "unauthorized")
            return

        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else -1
        except ValueError:
            self._json_error(400, "invalid content length")
            return
        if length < 0:
            self._json_error(400, "invalid content length")
            return
        if length > MAX_BODY:
            self._json_error(413, "request too large")
            return

        raw = self.rfile.read(length)

        try:
            body = json.loads(raw)
        except ValueError:
            self._json_error(400, "invalid json")
            return

        try:
            request = validate_request(body, app.config)
        except ValidationError as exc:
            self._json_error(400, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

        def write_chunk(data: bytes) -> None:
            self.wfile.write(b"%x\r\n" % len(data))
            self.wfile.write(data)
            self.wfile.write(b"\r\n")
            self.wfile.flush()

        def emit(event: dict) -> None:
            write_chunk(json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n")

        try:
            app.runner.run(request, emit)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
            return
        except Exception:
            logging.exception("sandbox run failed")
            try:
                emit({"type": "exit", "code": 1, "timed_out": False})
            except Exception:
                self.close_connection = True
                return

        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True


def _authorized(provided: str, expected: str) -> bool:
    try:
        return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))
    except Exception:
        return False


class _APIServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, _APIHandler)


class SandboxAPI:
    def __init__(self, config: Config, runner: SandboxRunner) -> None:
        self.config = config
        self.runner = runner
        self._server: _APIServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            return self.config.api_port
        return self._server.server_address[1]

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = _APIServer(("0.0.0.0", self.config.api_port), self)
        self._thread = threading.Thread(target=self._server.serve_forever, name="agent-api", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        server, self._server = self._server, None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
