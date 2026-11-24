# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Tests for the HTTP API."""

import http.client
import json
import os
import shutil
import tempfile
import time
import unittest

from runner.api import SandboxAPI
from runner.config import Config

TOKEN = "runner-token-that-is-long-enough-1234"


class FakeRunner:
    def __init__(self):
        self.requests = []

    def run(self, request, emit):
        self.requests.append(request)
        emit({"type": "started", "sandbox": "plane-agent-test"})
        emit({"type": "exit", "code": 0, "timed_out": False})


class APITests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "runs", "ws1", "tasks", "t1"))
        os.makedirs(os.path.join(self.root, "sessions", "u1"))

        self.config = Config(
            token=TOKEN,
            workspace_root=self.root,
            workspace_host_root="/srv/agents",
            api_port=0,
        )
        self.runner = FakeRunner()
        self.api = SandboxAPI(self.config, self.runner)
        self.api.start()
        self.addCleanup(self.api.shutdown)

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.api.port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def body(self):
        return {
            "sandbox_key": "task-42",
            "workspace": "runs/ws1/tasks/t1",
            "sessions": "sessions/u1",
            "session_id": "ses_abc123",
            "model": "gpt-5",
            "prompt": "hello",
            "title": "Fix it",
            "base_url": "https://opencode.ai/inference/go/secret",
            "api_key": "sk-real-secret",
        }

    def post(self, body, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = f"Bearer {TOKEN}"
        return self.request("POST", "/v1/sandboxes", body=body, headers=headers)

    def test_healthz(self):
        status, _, payload = self.request("GET", "/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"status": "ok"})

    def test_unauthorized_without_token(self):
        status, _, payload = self.post(json.dumps(self.body()).encode(), auth=False)
        self.assertEqual(status, 401)
        self.assertIn("error", json.loads(payload))
        self.assertEqual(self.runner.requests, [])

    def test_unauthorized_with_large_body_is_rejected_without_reading(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.api.port, timeout=5)
        try:
            started = time.monotonic()
            connection.putrequest("POST", "/v1/sandboxes")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(32 * 1024 * 1024))
            connection.endheaders()
            response = connection.getresponse()
            elapsed = time.monotonic() - started
            self.assertEqual(response.status, 401)
            response.read()
        finally:
            connection.close()
        self.assertLess(elapsed, 5)
        self.assertEqual(self.runner.requests, [])

    def test_invalid_json_is_400(self):
        status, _, payload = self.post(b"{")
        self.assertEqual(status, 400)
        self.assertIn("error", json.loads(payload))

    def test_validation_error_is_400(self):
        status, _, payload = self.post(json.dumps({"sandbox_key": "bad key"}).encode())
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload), {"error": "invalid sandbox_key"})
        self.assertEqual(self.runner.requests, [])

    def test_streams_ndjson_events(self):
        status, headers, payload = self.post(json.dumps(self.body()).encode())
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Content-Type"), "application/x-ndjson")
        self.assertEqual(headers.get("Transfer-Encoding"), "chunked")

        events = [json.loads(line) for line in payload.splitlines() if line]
        self.assertEqual(events[0], {"type": "started", "sandbox": "plane-agent-test"})
        self.assertEqual(events[-1], {"type": "exit", "code": 0, "timed_out": False})
        self.assertEqual(len(self.runner.requests), 1)
        self.assertEqual(self.runner.requests[0].api_key, "sk-real-secret")


if __name__ == "__main__":
    unittest.main()
