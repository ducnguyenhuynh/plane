# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Tests for request validation."""

import os
import shutil
import tempfile
import unittest

from runner.config import Config
from runner.validation import ValidationError, validate_request


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "runs", "ws1", "tasks", "t1"))
        os.makedirs(os.path.join(self.root, "sessions", "u1"))
        self.config = Config(token="t" * 32, workspace_root=self.root, workspace_host_root=self.root)

    def body(self, **overrides):
        body = {
            "sandbox_key": "key-1",
            "workspace": "runs/ws1/tasks/t1",
            "sessions": "sessions/u1",
            "session_id": "ses_abc123",
            "model": "gpt-5",
            "prompt": "hello",
            "title": "A title",
            "base_url": "https://opencode.ai/inference/go/secret/",
            "api_key": "sk-real-secret",
        }
        body.update(overrides)
        return body

    def assert_rejected(self, **overrides):
        with self.assertRaises(ValidationError) as ctx:
            validate_request(self.body(**overrides), self.config)
        self.assertNotIn("sk-real-secret", str(ctx.exception))

    def test_accepts_good_request(self):
        request = validate_request(self.body(), self.config)
        self.assertEqual(request.workspace, "runs/ws1/tasks/t1")
        self.assertFalse(request.read_only)
        self.assertEqual(request.effort, "")
        self.assertEqual(request.base_url, "https://opencode.ai/inference/go/secret")
        self.assertEqual(request.title, "A title")

    def test_rejects_path_traversal(self):
        self.assert_rejected(workspace="runs/../tasks/t1")

    def test_rejects_absolute_paths(self):
        self.assert_rejected(workspace="/etc")
        self.assert_rejected(sessions="/etc")

    def test_rejects_symlinked_workspace(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        os.makedirs(os.path.join(outside, "tasks", "t1"))
        os.symlink(outside, os.path.join(self.root, "runs", "link"))
        self.assert_rejected(workspace="runs/link/tasks/t1")

    def test_rejects_missing_directory(self):
        self.assert_rejected(sessions="sessions/absent")

    def test_rejects_bad_session_ids(self):
        for value in ("abc", "ses_", "ses_a-b", "ses_a.b"):
            with self.subTest(value=value):
                self.assert_rejected(session_id=value)

    def test_session_ids_over_80_chars_are_rejected(self):
        self.assert_rejected(session_id="ses_" + "a" * 77)
        request = validate_request(self.body(session_id="ses_" + "a" * 76), self.config)
        self.assertEqual(len(request.session_id), 80)

    def test_accepts_sandbox_settings(self):
        os.makedirs(os.path.join(self.root, "caches", "p1"))
        os.makedirs(os.path.join(self.root, "runs", "ws1", "skills"))
        request = validate_request(
            self.body(
                allowed_hosts=["PyPI.org", "pypi.org"],
                websearch=True,
                webfetch=True,
                deny_edit=True,
                cache="caches/p1",
                skills="runs/ws1/skills",
                mcp_servers=[
                    {
                        "name": "tools",
                        "url": "https://mcp.example.com/mcp",
                        "headers": {"Authorization": "Bearer x"},
                    }
                ],
            ),
            self.config,
        )

        self.assertEqual(request.allowed_hosts, ("pypi.org",))
        self.assertTrue(request.websearch)
        self.assertTrue(request.webfetch)
        self.assertTrue(request.deny_edit)
        self.assertEqual(request.cache, "caches/p1")
        server = request.mcp_servers[0]
        self.assertEqual(server.host, "mcp.example.com")
        self.assertEqual(server.path, "/mcp")
        self.assertEqual(server.headers, (("Authorization", "Bearer x"),))

    def test_rejects_bad_sandbox_settings(self):
        self.assert_rejected(allowed_hosts=["bad host"])
        self.assert_rejected(allowed_hosts="pypi.org")
        self.assert_rejected(allowed_hosts=["a" * 254])
        self.assert_rejected(websearch="yes")
        for url in (
            "http://mcp.example.com/mcp",
            "https://user:pass@mcp.example.com/mcp",
            "https://mcp.example.com/mcp?x=1",
            "https://mcp.example.com:8443/mcp",
        ):
            with self.subTest(url=url):
                self.assert_rejected(mcp_servers=[{"name": "tools", "url": url}])
        for headers in ({"Bad Header": "x"}, {"Host": "x"}, {"X-Test": "a\r\nb"}):
            with self.subTest(headers=headers):
                self.assert_rejected(
                    mcp_servers=[{"name": "tools", "url": "https://mcp.example.com/mcp", "headers": headers}]
                )
        self.assert_rejected(cache="caches/../etc")
        self.assert_rejected(skills="runs/ws1/tasks/t1")

    def test_rejects_non_opencode_host(self):
        self.assert_rejected(base_url="https://evil.example.com/inference/go/secret")

    def test_rejects_http_url(self):
        self.assert_rejected(base_url="http://opencode.ai/inference/go/secret")

    def test_rejects_userinfo(self):
        self.assert_rejected(base_url="https://user:pass@opencode.ai/inference/go/secret")

    def test_rejects_query_string(self):
        self.assert_rejected(base_url="https://opencode.ai/inference/go/secret?x=1")

    def test_rejects_wrong_path_prefix(self):
        self.assert_rejected(base_url="https://opencode.ai/v1/secret")

    def test_rejects_unsafe_base_url_paths(self):
        for path in (
            "/inference/go/../../x",
            "/inference/go/%2e%2e/x",
            "/inference/go//v1",
            "/inference/go",
            "/inference/go/",
        ):
            with self.subTest(path=path):
                self.assert_rejected(base_url=f"https://opencode.ai{path}")

    def test_accepts_nested_base_url_path(self):
        request = validate_request(self.body(base_url="https://opencode.ai/inference/go/v1"), self.config)
        self.assertEqual(request.base_url, "https://opencode.ai/inference/go/v1")

    def test_rejects_empty_prompt_and_bad_effort(self):
        self.assert_rejected(prompt="   ")
        self.assert_rejected(effort="turbo")

    def test_strips_control_chars_and_truncates_title(self):
        request = validate_request(self.body(title="a\x00b" * 100), self.config)
        self.assertNotIn("\x00", request.title)
        self.assertEqual(len(request.title), 120)

    def test_rejects_non_bool_read_only(self):
        self.assert_rejected(read_only="yes")


if __name__ == "__main__":
    unittest.main()
