# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Tests for the LLM proxy."""

import http.client
import json
import unittest

from runner.config import Config
from runner.llm_proxy import MAX_BODY, LLMProxy
from runner.validation import McpServer

MCP_SERVER = McpServer(
    name="tools",
    host="mcp.example.com",
    port=443,
    path="/mcp",
    headers=(("Authorization", "Bearer stored-secret"),),
)


class FakeResponse:
    def __init__(self, status=200, headers=None, chunks=None):
        self.status = status
        self._headers = headers or {}
        self._chunks = list(chunks or [])
        self.closed = False

    def getheader(self, name):
        for key, value in self._headers.items():
            if key.lower() == name.lower():
                return value
        return None

    def read1(self, size):
        return self._chunks.pop(0) if self._chunks else b""

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.calls = []
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class ProxyTests(unittest.TestCase):
    def setUp(self):
        self.upstream = FakeConnection(
            FakeResponse(
                headers={"Content-Type": "application/json"},
                chunks=[b"chunk-one", b"chunk-two"],
            )
        )
        self.config = Config(
            token="t" * 32,
            workspace_root="/tmp",
            workspace_host_root="/tmp",
            proxy_port=0,
        )
        self.proxy = LLMProxy(self.config, connection_factory=lambda host, port, timeout: self.upstream)
        self.proxy.start()
        self.addCleanup(self.proxy.stop)
        self.proxy.register("known-token", "https://opencode.ai/inference/go/secret", "sk-real-key", "ses_abc", "gpt-5")
        self.proxy.register(
            "mcp-token",
            "https://opencode.ai/inference/go/secret",
            "sk-real-key",
            "ses_abc",
            "gpt-5",
            mcp={"tools": MCP_SERVER},
        )

    def post(self, path, body=b"{}", headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=10)
        try:
            connection.request("POST", path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_forwards_with_real_key_and_drops_sandbox_auth(self):
        status, headers, payload = self.post(
            "/llm/known-token/chat/completions",
            body=b'{"model":"gpt-5"}',
            headers={
                "Authorization": "Bearer sandbox-value",
                "Content-Type": "application/json",
                "X-Other": "ignored",
            },
        )

        self.assertEqual(status, 200)
        self.assertEqual(payload, b"chunk-onechunk-two")
        self.assertEqual(headers.get("Content-Type"), "application/json")

        method, path, body, forwarded = self.upstream.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/inference/go/secret/chat/completions")
        self.assertEqual(body, b'{"model":"gpt-5"}')
        self.assertEqual(forwarded["Authorization"], "Bearer sk-real-key")
        self.assertEqual(forwarded["x-opencode-session"], "ses_abc")
        self.assertEqual(forwarded["User-Agent"], "plane-ai-agent/1.0")
        self.assertNotIn("X-Other", forwarded)
        self.assertNotIn("sandbox-value", " ".join(str(value) for value in forwarded.values()))

    def test_unknown_token_is_404(self):
        status, _, _ = self.post("/llm/other-token/chat/completions")
        self.assertEqual(status, 404)
        self.assertEqual(self.upstream.calls, [])

    def test_mcp_injects_the_stored_headers_and_drops_sandbox_headers(self):
        status, _, payload = self.post(
            "/mcp/mcp-token/tools",
            body=b'{"jsonrpc":"2.0"}',
            headers={
                "Authorization": "Bearer sandbox-value",
                "Content-Type": "application/json",
                "Mcp-Session-Id": "sid-1",
                "X-Other": "ignored",
            },
        )

        self.assertEqual(status, 200)
        method, path, body, forwarded = self.upstream.calls[0]
        self.assertEqual(method, "POST")
        self.assertEqual(path, "/mcp")
        self.assertEqual(body, b'{"jsonrpc":"2.0"}')
        self.assertEqual(forwarded["Authorization"], "Bearer stored-secret")
        self.assertEqual(forwarded["Mcp-Session-Id"], "sid-1")
        self.assertNotIn("X-Other", forwarded)
        self.assertNotIn("sandbox-value", " ".join(str(value) for value in forwarded.values()))

    def test_mcp_no_content_response_is_not_chunked(self):
        empty = FakeConnection(FakeResponse(status=204))
        proxy = LLMProxy(self.config, connection_factory=lambda host, port, timeout: empty)
        proxy.start()
        self.addCleanup(proxy.stop)
        proxy.register(
            "mcp-token",
            "https://opencode.ai/inference/go/secret",
            "sk-real-key",
            "ses_abc",
            "gpt-5",
            mcp={"tools": MCP_SERVER},
        )

        connection = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
        try:
            connection.request("GET", "/mcp/mcp-token/tools")
            response = connection.getresponse()
            self.assertEqual(response.status, 204)
            self.assertIsNone(response.getheader("Transfer-Encoding"))
            self.assertEqual(response.read(), b"")
            self.assertEqual(empty.calls[0][0], "GET")
        finally:
            connection.close()

    def test_mcp_redirect_becomes_502(self):
        redirect = FakeConnection(FakeResponse(status=302))
        proxy = LLMProxy(self.config, connection_factory=lambda host, port, timeout: redirect)
        proxy.start()
        self.addCleanup(proxy.stop)
        proxy.register(
            "mcp-token",
            "https://opencode.ai/inference/go/secret",
            "sk-real-key",
            "ses_abc",
            "gpt-5",
            mcp={"tools": MCP_SERVER},
        )

        connection = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
        try:
            connection.request("GET", "/mcp/mcp-token/tools")
            response = connection.getresponse()
            self.assertEqual(response.status, 502)
            response.read()
        finally:
            connection.close()

    def test_other_model_is_403(self):
        status, _, payload = self.post("/llm/known-token/chat/completions", body=b'{"model":"gpt-6"}')
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload), {"error": "model not allowed"})
        self.assertEqual(self.upstream.calls, [])

    def test_invalid_json_is_400(self):
        for body in (b"{", b"[]", b"null"):
            with self.subTest(body=body):
                status, _, _ = self.post("/llm/known-token/chat/completions", body=body)
                self.assertEqual(status, 400)
        self.assertEqual(self.upstream.calls, [])

    def test_other_paths_are_404(self):
        for path in ("/llm/known-token/models", "/v1/sandboxes", "/llm/known-token"):
            with self.subTest(path=path):
                status, _, _ = self.post(path)
                self.assertEqual(status, 404)
        self.assertEqual(self.upstream.calls, [])

    def test_oversize_body_is_413(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=10)
        try:
            connection.putrequest("POST", "/llm/known-token/chat/completions")
            connection.putheader("Content-Length", str(MAX_BODY + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            connection.close()
        self.assertEqual(self.upstream.calls, [])

    def test_missing_content_length_is_400(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.proxy.port, timeout=10)
        try:
            connection.putrequest("POST", "/llm/known-token/chat/completions")
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 400)
            response.read()
        finally:
            connection.close()

    def test_upstream_failure_is_502_without_key(self):
        def broken(host, port, timeout):
            raise OSError("no route")

        proxy = LLMProxy(self.config, connection_factory=broken)
        proxy.start()
        self.addCleanup(proxy.stop)
        proxy.register("tok", "https://opencode.ai/inference/go/secret", "sk-real-key", "ses_abc", "gpt-5")

        connection = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=10)
        try:
            connection.request(
                "POST",
                "/llm/tok/chat/completions",
                body=b'{"model":"gpt-5"}',
                headers={"Content-Type": "application/json"},
            )
            response = connection.getresponse()
            payload = response.read()
            self.assertEqual(response.status, 502)
            self.assertNotIn(b"sk-real-key", payload)
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
