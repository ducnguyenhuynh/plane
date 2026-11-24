# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Egress proxy for sandboxes: CONNECT to an allowlisted host on 443 only.

Every connection is resolved here, refused unless every address is public, and
then pinned to that address while TLS still verifies the real hostname.
"""

import base64
import http.client
import ipaddress
import select
import socket
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .config import Config

CONNECT_TIMEOUT = 15
IDLE_TIMEOUT = 300
TUNNEL_CHUNK = 65536


class EgressError(Exception):
    pass


class EgressDenied(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def resolve_public(host: str, port: int) -> str:
    """The first address of a host, refused unless every address is global."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressError("could not resolve host") from exc
    addresses = []
    for info in infos:
        address = info[4][0]
        try:
            parsed = ipaddress.ip_address(address)
        except ValueError as exc:
            raise EgressError("invalid address") from exc
        if not parsed.is_global:
            raise EgressError("host is not public")
        addresses.append(address)
    if not addresses:
        raise EgressError("could not resolve host")
    return addresses[0]


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    """TLS to a verified hostname, over a connection pinned to one address."""

    def __init__(self, host: str, port: int, timeout: int, address: str):
        super().__init__(host, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def open_https_connection(host: str, port: int, timeout: int) -> http.client.HTTPSConnection:
    return PinnedHTTPSConnection(host, port, timeout, resolve_public(host, port))


def basic_password(header: str) -> str | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8", "replace")
    except Exception:
        return None
    _, _, password = decoded.partition(":")
    return password or None


class _EgressHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        # Never log paths or authentication headers.
        return

    def do_CONNECT(self) -> None:  # noqa: N802 - stdlib signature
        app = self.server.app
        try:
            host = app.authorize(self.headers.get("Proxy-Authorization"), self.path)
        except EgressDenied as denied:
            self._error(denied.status, denied.message)
            return

        upstream = None
        try:
            upstream = socket.create_connection((resolve_public(host, 443), 443), timeout=CONNECT_TIMEOUT)
        except Exception:
            self._error(403, "forbidden")
            return

        try:
            self.send_response(200, "Connection established")
            self.end_headers()
            self._tunnel(upstream)
        except OSError:
            pass
        finally:
            try:
                upstream.close()
            except OSError:
                pass
            self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802 - stdlib signature
        self._error(405, "method not allowed")

    def do_HEAD(self) -> None:  # noqa: N802 - stdlib signature
        self._error(405, "method not allowed")

    def do_POST(self) -> None:  # noqa: N802 - stdlib signature
        self._error(405, "method not allowed")

    def do_PUT(self) -> None:  # noqa: N802 - stdlib signature
        self._error(405, "method not allowed")

    def do_PATCH(self) -> None:  # noqa: N802 - stdlib signature
        self._error(405, "method not allowed")

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib signature
        self._error(405, "method not allowed")

    def _tunnel(self, upstream: socket.socket) -> None:
        client = self.connection
        sockets = [client, upstream]
        last_activity = time.monotonic()
        while True:
            try:
                readable, _, failed = select.select(sockets, [], sockets, 1.0)
            except OSError:
                return
            if failed:
                return
            if not readable:
                if time.monotonic() - last_activity > IDLE_TIMEOUT:
                    return
                continue
            for source in readable:
                data = source.recv(TUNNEL_CHUNK)
                if not data:
                    return
                last_activity = time.monotonic()
                destination = upstream if source is client else client
                destination.sendall(data)

    def _error(self, status: int, message: str) -> None:
        payload = message.encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        except OSError:
            pass
        self.close_connection = True


class _EgressServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, app):
        self.app = app
        super().__init__(address, _EgressHandler)


class EgressProxy:
    """A CONNECT-only proxy that authorizes each tunnel with the sandbox token."""

    def __init__(self, config: Config, registry) -> None:
        self.config = config
        self.registry = registry
        self._server: _EgressServer | None = None
        self._thread: threading.Thread | None = None

    def authorize(self, proxy_authorization: str | None, path: str) -> str:
        token = basic_password(proxy_authorization)
        target = self.registry.get(token) if token else None
        if target is None:
            raise EgressDenied(407, "proxy authentication required")

        host, _, port = path.partition(":")
        if port != "443" or not host or host.lower() not in target.allowed_hosts:
            raise EgressDenied(403, "forbidden")
        return host

    @property
    def port(self) -> int:
        if self._server is None:
            return self.config.egress_port
        return self._server.server_address[1]

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = _EgressServer(("0.0.0.0", self.config.egress_port), self)
        self._thread = threading.Thread(target=self._server.serve_forever, name="egress-proxy", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        server, self._server = self._server, None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
