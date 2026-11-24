# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Tests for Docker argv construction and the sandbox runner."""

import dataclasses
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from runner.config import Config
from runner.sandbox import (
    DRAIN_TIMEOUT,
    SandboxRunner,
    build_docker_args,
    build_opencode_config,
    reap_stale_sandboxes,
)
from runner.validation import McpServer, SandboxRequest

MCP_SERVER = McpServer(
    name="tools",
    host="mcp.example.com",
    port=443,
    path="/mcp",
    headers=(("Authorization", "Bearer stored-secret"),),
)


def make_request(**overrides) -> SandboxRequest:
    request = SandboxRequest(
        sandbox_key="task-42",
        workspace="runs/ws1/tasks/t1",
        sessions="sessions/u1",
        read_only=False,
        session_id="ses_abc123",
        model="gpt-5",
        effort="",
        prompt="please do the thing",
        title="Fix the bug",
        base_url="https://opencode.ai/inference/go/secret",
        api_key="sk-real-secret",
    )
    return dataclasses.replace(request, **overrides)


class FakeProxy:
    def __init__(self):
        self.tokens = {}

    def register(self, token, base_url, api_key, session_id, model, allowed_hosts=(), mcp=None):
        self.tokens[token] = (base_url, api_key, session_id, model, tuple(allowed_hosts), dict(mcp or {}))

    def unregister(self, token):
        self.tokens.pop(token, None)


class SandboxTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def make_config(self, **overrides) -> Config:
        values = {
            "token": "t" * 32,
            "workspace_root": self.root,
            "workspace_host_root": "/srv/agents",
            "max_sandboxes": 2,
            "idle_timeout": 30,
        }
        values.update(overrides)
        return Config(**values)

    def test_opencode_config_has_no_api_key(self):
        config = build_opencode_config(make_request(), "http://agent-runner:8788/llm/token")
        dumped = json.dumps(config)
        self.assertNotIn("sk-real-secret", dumped)
        self.assertEqual(config["provider"]["plane"]["options"]["apiKey"], "sandbox")
        self.assertEqual(config["provider"]["plane"]["options"]["baseURL"], "http://agent-runner:8788/llm/token")
        self.assertEqual(config["model"], "plane/gpt-5")
        self.assertFalse(config["autoupdate"])
        self.assertEqual(config["share"], "disabled")
        self.assertEqual(config["enabled_providers"], ["plane"])
        self.assertNotIn("edit", config["permission"])
        self.assertEqual(config["permission"]["external_directory"], "deny")

    def test_opencode_config_read_only_and_effort(self):
        request = make_request(read_only=True, effort="high")
        config = build_opencode_config(request, "http://agent-runner:8788/llm/token")
        self.assertEqual(config["permission"]["edit"], "deny")
        entry = config["provider"]["plane"]["models"]["gpt-5"]
        self.assertEqual(entry["options"], {"reasoningEffort": "high"})
        self.assertTrue(entry["reasoning"])
        self.assertEqual(entry["limit"], {"context": 1000000, "output": 384000})

    def test_opencode_config_caps_claude_output_tokens(self):
        claude = build_opencode_config(make_request(model="claude-haiku-5-5"), "http://agent-runner:8788/llm/token")
        entry = claude["provider"]["plane"]["models"]["claude-haiku-5-5"]
        self.assertEqual(entry["options"]["max_tokens"], 128000)
        self.assertEqual(entry["limit"]["output"], 128000)

        deepseek = build_opencode_config(
            make_request(model="deepseek-v4.1-flash"), "http://agent-runner:8788/llm/token"
        )
        entry = deepseek["provider"]["plane"]["models"]["deepseek-v4.1-flash"]
        self.assertNotIn("max_tokens", entry.get("options") or {})
        self.assertEqual(entry["limit"]["output"], 384000)

    def test_docker_args_carry_security_flags(self):
        config = self.make_config()
        request = make_request()
        args = build_docker_args(request, config, "plane-agent-task-42-deadbeef")
        joined = " ".join(args)

        for flag in (
            "--read-only",
            "--cap-drop",
            "--security-opt",
            "--pids-limit",
            "--memory-swap",
            "--cpus",
            "--user",
        ):
            self.assertIn(flag, args)
        self.assertEqual(args[args.index("--cap-drop") + 1], "ALL")
        self.assertEqual(args[args.index("--security-opt") + 1], "no-new-privileges")
        self.assertEqual(args[args.index("--user") + 1], "10001:10001")
        self.assertIn("plane.agent.sandbox=1", args)
        self.assertIn("--env", args)
        self.assertIn("OPENCODE_CONFIG_CONTENT", args)
        self.assertIn("plane-agent-sandbox:2.2.6", args)
        self.assertIn("plane/gpt-5", args)
        self.assertIn("ses_abc123", args)
        self.assertIn("Fix the bug", args)

        self.assertNotIn("docker.sock", joined)
        self.assertNotIn("--privileged", args)
        self.assertNotIn(request.api_key, joined)
        self.assertNotIn(request.prompt, joined)

    def test_mcp_config_points_at_the_proxy_and_never_leaks_secrets(self):
        request = make_request(
            mcp_servers=(MCP_SERVER,),
            skills="runs/run-1/skills",
            cache="caches/p1",
        )
        config = build_opencode_config(request, "http://agent-runner:8788/llm/token-1")
        entry = config["mcp"]["servers"]["tools"]
        self.assertEqual(entry["url"], "http://agent-runner:8788/mcp/token-1/tools")
        self.assertFalse(entry["oauth"])
        self.assertFalse(entry["codemode"])
        self.assertEqual(config["skills"], ["/home/agent/.plane-skills"])
        self.assertNotIn("stored-secret", json.dumps(config))

        args = build_docker_args(request, self.make_config(), "c")
        self.assertNotIn("stored-secret", " ".join(args))
        self.assertNotIn("token-1", " ".join(args))
        mounts = [args[i + 1] for i, value in enumerate(args) if value == "--mount"]
        self.assertIn("type=bind,src=/srv/agents/caches/p1,dst=/home/agent/.package-cache", mounts)
        self.assertIn("type=bind,src=/srv/agents/runs/run-1/skills,dst=/home/agent/.plane-skills,readonly", mounts)
        self.assertNotIn("type=bind,src=/srv/agents/caches/p1,dst=/home/agent/.cache", mounts)
        self.assertIn("/home/agent/.cache:rw,nosuid,nodev,uid=10001,gid=10001,size=1g", args)
        self.assertIn("HTTP_PROXY", args)
        self.assertIn("NO_PROXY", args)

    def test_a_read_only_request_mounts_no_package_cache(self):
        request = make_request(read_only=True, cache="caches/p1")
        args = build_docker_args(request, self.make_config(), "c")

        mounts = [args[i + 1] for i, value in enumerate(args) if value == "--mount"]
        self.assertFalse(any("/home/agent/.package-cache" in mount for mount in mounts))
        self.assertIn("/home/agent/.cache:rw,nosuid,nodev,uid=10001,gid=10001,size=1g", args)

    def test_read_only_denies_the_mcp_tools(self):
        request = make_request(read_only=True, mcp_servers=(MCP_SERVER,))
        config = build_opencode_config(request, "http://agent-runner:8788/llm/token")
        self.assertEqual(config["permission"]["tools_*"], "deny")
        self.assertEqual(config["permission"]["edit"], "deny")

        config = build_opencode_config(make_request(mcp_servers=(MCP_SERVER,)), "http://agent-runner:8788/llm/token")
        self.assertNotIn("tools_*", config["permission"])

    def test_docker_args_mounts(self):
        config = self.make_config()
        args = build_docker_args(make_request(), config, "c")
        mounts = [args[i + 1] for i, value in enumerate(args) if value == "--mount"]
        self.assertEqual(
            mounts,
            [
                "type=bind,src=/srv/agents/runs/ws1/tasks/t1,dst=/workspace",
                "type=bind,src=/srv/agents/sessions/u1,dst=/home/agent/.local/share/opencode",
            ],
        )

        args = build_docker_args(make_request(read_only=True), config, "c")
        mounts = [args[i + 1] for i, value in enumerate(args) if value == "--mount"]
        self.assertEqual(mounts[0], "type=bind,src=/srv/agents/runs/ws1/tasks/t1,dst=/workspace,readonly")

    def write_fake_docker(self, body: str) -> str:
        path = os.path.join(self.root, "docker-fake.sh")
        script = "#!/bin/sh\nset -eu\nDIR='" + self.root + "'\n" + body
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(script)
        os.chmod(path, 0o755)
        return path

    def run_script(self) -> str:
        return """cmd="$1"
shift
case "$cmd" in
  run)
    name=""
    prev=""
    for a in "$@"; do
      if [ "$prev" = "--name" ]; then name="$a"; fi
      prev="$a"
    done
    echo "$name" > "$DIR/name.txt"
    cat > "$DIR/stdin.txt"
    printf '%s\\n' '{"type":"message","text":"hello"}'
    printf '%s\\n' 'plain log line'
    printf '%s\\n' 'stderr line' >&2
    ;;
  rm)
    echo "$*" >> "$DIR/rm.txt"
    ;;
  ps)
    ;;
esac
exit 0
"""

    def test_runner_streams_events(self):
        docker = self.write_fake_docker(self.run_script())
        config = self.make_config(docker_bin=docker)
        proxy = FakeProxy()
        runner = SandboxRunner(config, proxy)
        events = []

        runner.run(make_request(), events.append)

        self.assertEqual(events[0]["type"], "started")
        name = events[0]["sandbox"]
        self.assertTrue(name.startswith("plane-agent-task-42-"))
        self.assertTrue(any(e.get("type") == "event" and e["event"]["type"] == "message" for e in events))
        self.assertTrue(
            any(
                e.get("type") == "log" and e.get("stream") == "stdout" and e.get("line") == "plain log line"
                for e in events
            )
        )
        self.assertTrue(
            any(
                e.get("type") == "log" and e.get("stream") == "stderr" and e.get("line") == "stderr line"
                for e in events
            )
        )
        self.assertEqual(events[-1], {"type": "exit", "code": 0, "timed_out": False})

        with open(os.path.join(self.root, "stdin.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), make_request().prompt)
        self.assertEqual(proxy.tokens, {})

        with open(os.path.join(self.root, "rm.txt"), encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn(name, lines[0])

    def test_runner_removes_container_when_client_disconnects(self):
        docker = self.write_fake_docker(
            """case "$1" in
  run) sleep 5 ;;
  rm) echo "$*" >> "$DIR/rm.txt" ;;
esac
exit 0
"""
        )
        config = self.make_config(docker_bin=docker)
        proxy = FakeProxy()
        runner = SandboxRunner(config, proxy)

        def emit(event):
            raise ConnectionResetError("gone")

        started = time.monotonic()
        runner.run(make_request(), emit)
        self.assertLess(time.monotonic() - started, 5)

        with open(os.path.join(self.root, "rm.txt"), encoding="utf-8") as handle:
            self.assertIn("plane-agent-task-42-", handle.read())
        self.assertEqual(proxy.tokens, {})

    def test_runner_enforces_idle_timeout(self):
        docker = self.write_fake_docker(
            """case "$1" in
  run) sleep 30 ;;
  rm) echo "$*" >> "$DIR/rm.txt" ;;
esac
exit 0
"""
        )
        config = self.make_config(docker_bin=docker, idle_timeout=1)
        runner = SandboxRunner(config, FakeProxy())
        events = []

        started = time.monotonic()
        runner.run(make_request(), events.append)
        self.assertLess(time.monotonic() - started, 10)

        self.assertEqual(events[-1]["type"], "exit")
        self.assertTrue(events[-1]["timed_out"])
        self.assertNotEqual(events[-1]["code"], 0)

        with open(os.path.join(self.root, "rm.txt"), encoding="utf-8") as handle:
            self.assertIn(f"rm -f {events[0]['sandbox']}", handle.read())

    def test_runner_enforces_concurrency_limit(self):
        docker = self.write_fake_docker(
            """case "$1" in
  run)
    echo "$DIR/start-$$" >> "$DIR/trace.txt"
    sleep 0.4
    ;;
  rm) ;;
esac
exit 0
"""
        )
        config = self.make_config(docker_bin=docker, max_sandboxes=1)
        runner = SandboxRunner(config, FakeProxy())
        results = []

        def worker():
            events = []
            runner.run(make_request(), events.append)
            results.append(events[-1]["type"])

        started = time.monotonic()
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        elapsed = time.monotonic() - started

        self.assertEqual(results, ["exit", "exit"])
        self.assertGreaterEqual(elapsed, 0.7)

    def test_supervise_drains_readers_before_returning(self):
        runner = SandboxRunner(self.make_config(docker_bin="/bin/true"), FakeProxy())
        proc = subprocess.Popen(
            ["sh", "-c", "printf 'out\\n'; printf 'err\\n' >&2"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(proc.wait)
        events = []
        joins = []
        real_join = threading.Thread.join

        def spy(thread, timeout=None):
            joins.append(timeout)
            return real_join(thread, timeout)

        with patch.object(threading.Thread, "join", spy):
            runner._supervise(proc, events.append, threading.Event(), threading.Event(), [time.monotonic()], [False])

        lines = [event["line"] for event in events if event["type"] == "log"]
        self.assertEqual(sorted(lines), ["err", "out"])
        # Both readers drain with DRAIN_TIMEOUT; the monitor keeps a short join
        self.assertEqual(joins.count(DRAIN_TIMEOUT), 2)
        self.assertEqual(joins.count(5), 1)

    def test_reap_stale_sandboxes(self):
        docker = self.write_fake_docker(
            """case "$1" in
  ps) printf '%s\\n' abc123 def456 ;;
  rm) echo "$*" >> "$DIR/rm.txt" ;;
esac
exit 0
"""
        )
        reap_stale_sandboxes(self.make_config(docker_bin=docker))
        with open(os.path.join(self.root, "rm.txt"), encoding="utf-8") as handle:
            lines = [line for line in handle.read().splitlines() if line]
        self.assertEqual(len(lines), 2)
        self.assertTrue(any("abc123" in line for line in lines))
        self.assertTrue(any("def456" in line for line in lines))


if __name__ == "__main__":
    unittest.main()
