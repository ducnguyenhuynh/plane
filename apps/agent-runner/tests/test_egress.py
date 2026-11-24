# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Tests for the sandbox egress proxy."""

import base64
import socket
import unittest
from unittest.mock import patch

from runner.config import Config
from runner.egress import EgressDenied, EgressError, EgressProxy, resolve_public
from runner.llm_proxy import ProxyRegistry


def basic(token):
    return "Basic " + base64.b64encode(f"sandbox:{token}".encode()).decode()


class EgressTests(unittest.TestCase):
    def setUp(self):
        self.config = Config(token="t" * 32, workspace_root="/tmp", workspace_host_root="/tmp", egress_port=0)
        self.registry = ProxyRegistry()
        self.registry.register(
            "sandbox-token",
            "https://opencode.ai/inference/go/secret",
            "key",
            "ses_abc",
            "gpt-5",
            allowed_hosts=("pypi.org",),
        )
        self.proxy = EgressProxy(self.config, self.registry)

    def test_allows_a_listed_host(self):
        self.assertEqual(self.proxy.authorize(basic("sandbox-token"), "pypi.org:443"), "pypi.org")

    def test_refuses_an_unlisted_host_and_another_port(self):
        for path in ("evil.example.com:443", "pypi.org:80", "pypi.org"):
            with self.subTest(path=path):
                with self.assertRaises(EgressDenied) as caught:
                    self.proxy.authorize(basic("sandbox-token"), path)
                self.assertEqual(caught.exception.status, 403)

    def test_refuses_a_missing_or_wrong_token(self):
        for header in (None, "", "Basic !!!", basic("other-token")):
            with self.subTest(header=header):
                with self.assertRaises(EgressDenied) as caught:
                    self.proxy.authorize(header, "pypi.org:443")
                self.assertEqual(caught.exception.status, 407)

    def test_resolution_refuses_a_private_address(self):
        private = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]
        with patch("runner.egress.socket.getaddrinfo", return_value=private):
            with self.assertRaises(EgressError):
                resolve_public("pypi.org", 443)

    def test_resolution_refuses_a_mixed_answer(self):
        mixed = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        ]
        with patch("runner.egress.socket.getaddrinfo", return_value=mixed):
            with self.assertRaises(EgressError):
                resolve_public("pypi.org", 443)


if __name__ == "__main__":
    unittest.main()
