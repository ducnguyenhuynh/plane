# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""LLM proxy for sandboxes.

Each sandbox gets a random token. The token selects the upstream base URL and
the real provider key, which are injected here and never leave the runner. The
sandbox's own Authorization header is discarded.
"""

import json
import logging
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .config import Config
from .egress import open_https_connection

MAX_BODY = 64 * 1024 * 1024
UPSTREAM_TIMEOUT = 900
CHUNK_SIZE = 65536
API_KEY_HEADER = "Bearer"
MCP_FORWARD_HEADERS = ("Content-Type", "Accept", "Mcp-Session-Id", "MCP-Protocol-Version", "Last-Event-ID")


@dataclass(frozen=True)
class ProxyTarget:
    host: str
    port: int
    path: str
    api_key: str
    session_id: str
    model: str
    allowed_hosts: frozenset = frozenset()
    # name -> validation.McpServer (url parts and secret headers)
    mcp: dict = field(default_factory=dict)


class ProxyRegistry:
    """Thread-safe token -> upstream mapping. Tokens are never logged."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._targets: dict[str, ProxyTarget] = {}

    def register(
        self,
        token: str,
        base_url: str,
        api_key: str,
        session_id: str,
        model: str,
        allowed_hosts=(),
        mcp=None,
    ) -> None:
        parsed = urlsplit(base_url)
        target = ProxyTarget(
            host=parsed.hostname or "",
            port=parsed.port or 443,
            path=parsed.path.rstrip("/") + "/chat/completions",
            api_key=api_key,
            session_id=session_id,
            model=model,
            allowed_hosts=frozenset(allowed_hosts),
            mcp=dict(mcp or {}),
        )
        with self._lock:
            self._targets[token] = target

    def unregister(self, token: str) -> None:
        with self._lock:
            self._targets.pop(token, None)

    def get(self, token: str) -> ProxyTarget | None:
        with self._lock:
            return self._targets.get(token)


def _extract_token(path: str) -> str | None:
    prefix = "/llm/"
    suffix = "/chat/completions"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    token = path[len(prefix) : -len(suffix)]
    if not token or "/" in token:
        return None
    return token


def _extract_mcp(path: str) -> tuple[str, str] | None:
    prefix = "/mcp/"
    if not path.startswith(prefix):
        return None
    parts = path[len(prefix) :].split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        return None
    return parts[0], parts[1]


class _ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        # Never log paths (they contain tokens) or request bodies.
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        if self.path.startswith("/mcp/"):
            self._handle_mcp("POST")
            return

        app = self.server.app

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

        body = self.rfile.read(length)
        if len(body) != length:
            self._json_error(400, "incomplete body")
            return

        token = _extract_token(self.path)
        target = app.registry.get(token) if token is not None else None
        if target is None:
            self._json_error(404, "not found")
            return

        try:
            payload = json.loads(body)
        except ValueError:
            self._json_error(400, "invalid json")
            return
        if not isinstance(payload, dict):
            self._json_error(400, "invalid json")
            return
        if payload.get("model") != target.model:
            self._json_error(403, "model not allowed")
            return

        self._forward(app, target, body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        if self.path.startswith("/mcp/"):
            self._handle_mcp("GET")
            return
        self._json_error(404, "not found")

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        self._json_error(404, "not found")

    def do_PUT(self) -> None:  # noqa: N802 - stdlib signature
        self._json_error(404, "not found")

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib signature
        self._json_error(404, "not found")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib signature
        if self.path.startswith("/mcp/"):
            self._handle_mcp("DELETE")
            return
        self._json_error(404, "not found")

    def _handle_mcp(self, method: str) -> None:
        app = self.server.app
        parsed = _extract_mcp(self.path)
        token, server_name = parsed if parsed is not None else (None, None)
        target = app.registry.get(token) if token is not None else None
        server = target.mcp.get(server_name) if target is not None and server_name is not None else None
        if server is None:
            self._json_error(404, "not found")
            return

        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length is not None else 0
        except ValueError:
            self._json_error(400, "invalid content length")
            return
        if length < 0:
            self._json_error(400, "invalid content length")
            return
        if length > MAX_BODY:
            self._json_error(413, "request too large")
            return

        body = self.rfile.read(length) if length else b""
        if len(body) != length:
            self._json_error(400, "incomplete body")
            return

        # Only safe headers pass through; the stored secret headers are added here
        headers = {"User-Agent": "plane-ai-agent/1.0"}
        for header_name in MCP_FORWARD_HEADERS:
            value = self.headers.get(header_name)
            if value is not None:
                headers[header_name] = value
        for header_name, header_value in server.headers:
            headers[header_name] = header_value

        connection = None
        upstream = None
        try:
            try:
                connection = app.connection_factory(server.host, server.port, UPSTREAM_TIMEOUT)
                connection.request(method, server.path, body=body if length else None, headers=headers)
                upstream = connection.getresponse()
            except Exception:
                logging.warning("mcp proxy upstream request failed")
                self._json_error(502, "upstream unavailable")
                return

            # A redirect is never followed: the sandbox may only reach the stored host
            if 300 <= upstream.status < 400:
                self._json_error(502, "upstream redirect")
                return

            self.send_response(upstream.status)
            for header_name in ("Content-Type", "Mcp-Session-Id"):
                value = upstream.getheader(header_name)
                if value:
                    self.send_header(header_name, value)
            if upstream.status in (204, 304):
                # HTTP forbids a body for these statuses: chunked framing would corrupt the connection
                self.end_headers()
                self.close_connection = True
                return
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self._stream(upstream)
        finally:
            for resource in (upstream, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    def _stream(self, upstream) -> None:
        try:
            while True:
                chunk = upstream.read1(CHUNK_SIZE)
                if not chunk:
                    break
                self.wfile.write(b"%x\r\n" % len(chunk))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def _forward(self, app, target: ProxyTarget, body: bytes) -> None:
        headers = {
            "Authorization": f"{API_KEY_HEADER} {target.api_key}",
            "Content-Type": self.headers.get("Content-Type") or "application/json",
            "Accept": self.headers.get("Accept") or "application/json",
            "x-opencode-session": target.session_id,
            "User-Agent": "plane-ai-agent/1.0",
        }

        connection = None
        upstream = None
        try:
            try:
                connection = app.connection_factory(target.host, target.port, UPSTREAM_TIMEOUT)
                connection.request("POST", target.path, body=body, headers=headers)
                upstream = connection.getresponse()
            except Exception:
                logging.warning("llm proxy upstream request failed")
                self._json_error(502, "upstream unavailable")
                return

            self.send_response(upstream.status)
            content_type = upstream.getheader("Content-Type")
            if content_type:
                self.send_header("Content-Type", content_type)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()

            self._stream(upstream)
        finally:
            for resource in (upstream, connection):
                if resource is not None:
                    try:
                        resource.close()
                    except Exception:
                        pass

    def _json_error(self, status: int, message: str) -> None:
        payload = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        self.close_connection = True


class _ProxyHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, _ProxyHandler)


class LLMProxy:
    def __init__(self, config: Config, connection_factory=None) -> None:
        self.config = config
        self.registry = ProxyRegistry()
        self.connection_factory = connection_factory or open_https_connection
        self._server: _ProxyHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def register(
        self,
        token: str,
        base_url: str,
        api_key: str,
        session_id: str,
        model: str,
        allowed_hosts=(),
        mcp=None,
    ) -> None:
        self.registry.register(token, base_url, api_key, session_id, model, allowed_hosts=allowed_hosts, mcp=mcp)

    def unregister(self, token: str) -> None:
        self.registry.unregister(token)

    @property
    def port(self) -> int:
        if self._server is None:
            return self.config.proxy_port
        return self._server.server_address[1]

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = _ProxyHTTPServer(("0.0.0.0", self.config.proxy_port), self)
        self._thread = threading.Thread(target=self._server.serve_forever, name="llm-proxy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
