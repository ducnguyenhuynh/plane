# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Sandbox lifecycle: build the Docker argv, run the CLI, stream events.

The provider API key never reaches the sandbox. It stays in the runner, which
registers a per-sandbox proxy token and hands the CLI a localhost-style URL.
"""

import json
import logging
import os
import secrets
import signal
import subprocess
import threading
import time
from typing import Callable

from .config import Config
from .validation import SandboxRequest

HEARTBEAT_INTERVAL = 15.0
# Longer than the worker's 60s read timeout, so a dead client cannot hold a slot forever
DRAIN_TIMEOUT = 120

Emit = Callable[[dict], None]


class ClientGone(Exception):
    """The API client disconnected; stop streaming and drop the container."""


def build_opencode_config(request: SandboxRequest, proxy_base_url: str) -> dict:
    options: dict = {}
    if request.effort:
        options["reasoningEffort"] = request.effort
    if request.model.startswith("claude-"):
        # shortcut: assumes the 128K output cap of current Claude models, add a per-model table if an older one is used.
        options["max_tokens"] = 128000

    output_limit = 128000 if request.model.startswith("claude-") else 384000
    model_entry: dict = {
        "name": request.model,
        "reasoning": True,
        "limit": {"context": 1000000, "output": output_limit},
    }
    if options:
        model_entry["options"] = options

    permission: dict = {"external_directory": "deny"}
    if request.read_only or request.deny_edit:
        permission["edit"] = "deny"
    if not request.websearch:
        permission["websearch"] = "deny"
    if not request.webfetch:
        permission["webfetch"] = "deny"

    mcp_root, _, mcp_token = proxy_base_url.partition("/llm/")
    mcp_servers = {
        server.name: {
            "type": "remote",
            "url": f"{mcp_root}/mcp/{mcp_token}/{server.name}",
            "oauth": False,
            "codemode": False,
        }
        for server in request.mcp_servers
    }
    if mcp_servers and (request.read_only or request.deny_edit):
        for server_name in mcp_servers:
            permission[f"{server_name}_*"] = "deny"

    config = {
        "autoupdate": False,
        "share": "disabled",
        "enabled_providers": ["plane"],
        "model": f"plane/{request.model}",
        "small_model": f"plane/{request.model}",
        "permission": permission,
        "websearch": {"provider": "exa"} if request.websearch else False,
        "provider": {
            "plane": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Plane",
                "options": {"baseURL": proxy_base_url, "apiKey": "sandbox"},
                "models": {request.model: model_entry},
            }
        },
    }
    if request.skills:
        config["skills"] = ["/home/agent/.plane-skills"]
    if mcp_servers:
        config["mcp"] = {"servers": mcp_servers}
    return config


def build_docker_args(request: SandboxRequest, config: Config, container_name: str) -> list[str]:
    workspace_src = os.path.join(config.workspace_host_root, request.workspace)
    sessions_src = os.path.join(config.workspace_host_root, request.sessions)
    workspace_mount = f"type=bind,src={workspace_src},dst=/workspace"
    if request.read_only:
        workspace_mount += ",readonly"

    cache_args = ["--tmpfs", f"/home/agent/.cache:rw,nosuid,nodev,uid=10001,gid=10001,size={config.tmp_size}"]
    if request.cache and not request.read_only:
        # shortcut: the cache has no size limit and shares the 20G workspace disk, add eviction when a project fills it.
        cache_src = os.path.join(config.workspace_host_root, request.cache)
        cache_args += ["--mount", f"type=bind,src={cache_src},dst=/home/agent/.package-cache"]

    skills_args = []
    if request.skills:
        skills_src = os.path.join(config.workspace_host_root, request.skills)
        skills_args = ["--mount", f"type=bind,src={skills_src},dst=/home/agent/.plane-skills,readonly"]

    return [
        config.docker_bin,
        "run",
        "--rm",
        "-i",
        "--name",
        container_name,
        "--label",
        "plane.agent.sandbox=1",
        "--network",
        config.network,
        "--user",
        "10001:10001",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        str(config.pids),
        "--memory",
        config.memory,
        "--memory-swap",
        config.memory,
        "--cpus",
        config.cpus,
        "--read-only",
        "--tmpfs",
        f"/tmp:rw,nosuid,nodev,size={config.tmp_size}",
        *cache_args,
        "--tmpfs",
        "/home/agent/.config:rw,nosuid,nodev,uid=10001,gid=10001,size=16m",
        "--tmpfs",
        "/home/agent/.local/state:rw,nosuid,nodev,uid=10001,gid=10001,size=64m",
        "--mount",
        workspace_mount,
        "--mount",
        f"type=bind,src={sessions_src},dst=/home/agent/.local/share/opencode",
        *skills_args,
        "--workdir",
        "/workspace",
        "--env",
        "OPENCODE_CONFIG_CONTENT",
        "--env",
        "OPENCODE_DISABLE_AUTOUPDATE=1",
        "--env",
        "OPENCODE_DISABLE_LSP_DOWNLOAD=1",
        "--env",
        "OPENCODE_DISABLE_MODELS_FETCH=1",
        # Values stay in the docker CLI environment; argv only carries the names
        "--env",
        "HTTP_PROXY",
        "--env",
        "HTTPS_PROXY",
        "--env",
        "http_proxy",
        "--env",
        "https_proxy",
        "--env",
        "NO_PROXY",
        "--env",
        "no_proxy",
        config.sandbox_image,
        "--model",
        f"plane/{request.model}",
        "--session",
        request.session_id,
        "--title",
        request.title,
    ]


def _subprocess_env(extra: dict | None = None) -> dict:
    env = {}
    for name in ("PATH", "HOME", "DOCKER_HOST"):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    if extra:
        env.update(extra)
    return env


def _parse_event(line: str) -> dict | None:
    line = line.strip()
    if not line:
        return None
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _feed_prompt(proc: subprocess.Popen, prompt: str) -> None:
    try:
        if proc.stdin is not None:
            proc.stdin.write(prompt)
            proc.stdin.flush()
    except (BrokenPipeError, OSError, ValueError):
        pass
    finally:
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except (BrokenPipeError, OSError, ValueError):
            pass


def _kill(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is not None:
            return
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def _close_streams(proc: subprocess.Popen) -> None:
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass


class SandboxRunner:
    def __init__(self, config: Config, proxy) -> None:
        self.config = config
        self.proxy = proxy
        self._slots = threading.BoundedSemaphore(config.max_sandboxes)

    def run(self, request: SandboxRequest, emit: Emit) -> None:
        emit_lock = threading.Lock()
        dead = threading.Event()

        def send(event: dict) -> None:
            with emit_lock:
                if dead.is_set():
                    raise ClientGone()
                try:
                    emit(event)
                except BaseException as exc:
                    dead.set()
                    raise ClientGone() from exc

        while not self._slots.acquire(timeout=HEARTBEAT_INTERVAL):
            send({"type": "heartbeat"})

        token = secrets.token_urlsafe(32)
        name = f"plane-agent-{request.sandbox_key[:60]}-{secrets.token_hex(4)}"
        proc: subprocess.Popen | None = None
        registered = False
        stop = threading.Event()
        client_gone = threading.Event()
        last_output = [time.monotonic()]
        timed_out = [False]

        try:
            self.proxy.register(
                token,
                request.base_url,
                request.api_key,
                request.session_id,
                request.model,
                allowed_hosts=request.allowed_hosts,
                mcp={server.name: server for server in request.mcp_servers},
            )
            registered = True

            proxy_base_url = f"http://{self.config.proxy_host}:{self.config.proxy_port}/llm/{token}"
            container_config = json.dumps(build_opencode_config(request, proxy_base_url))
            sandbox_proxy = f"http://sandbox:{token}@{self.config.proxy_host}:{self.config.egress_port}"
            no_proxy = f"{self.config.proxy_host},localhost,127.0.0.1"

            try:
                proc = subprocess.Popen(
                    build_docker_args(request, self.config, name),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=_subprocess_env(
                        {
                            "OPENCODE_CONFIG_CONTENT": container_config,
                            "HTTP_PROXY": sandbox_proxy,
                            "HTTPS_PROXY": sandbox_proxy,
                            "http_proxy": sandbox_proxy,
                            "https_proxy": sandbox_proxy,
                            "NO_PROXY": no_proxy,
                            "no_proxy": no_proxy,
                        }
                    ),
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    start_new_session=True,
                )
            except OSError:
                logging.exception("failed to start sandbox container")
                try:
                    send({"type": "exit", "code": 127, "timed_out": False})
                except ClientGone:
                    pass
                return

            threading.Thread(target=_feed_prompt, args=(proc, request.prompt), daemon=True).start()

            try:
                send({"type": "started", "sandbox": name})
            except ClientGone:
                client_gone.set()

            if not client_gone.is_set():
                self._supervise(proc, send, stop, client_gone, last_output, timed_out)

            try:
                send({"type": "exit", "code": proc.returncode, "timed_out": timed_out[0]})
            except ClientGone:
                pass
        finally:
            stop.set()
            if registered:
                self.proxy.unregister(token)
            if proc is not None:
                self._terminate(proc)
                _close_streams(proc)
                # The container outlives the `docker run` CLI, so always remove it:
                # --rm is a no-op when the CLI already exited normally.
                self._remove_container(name)
            self._slots.release()

    def _supervise(
        self,
        proc: subprocess.Popen,
        send: Emit,
        stop: threading.Event,
        client_gone: threading.Event,
        last_output: list[float],
        timed_out: list[bool],
    ) -> None:
        def fail() -> None:
            client_gone.set()
            stop.set()
            _kill(proc)

        def read(stream, stream_name: str) -> None:
            try:
                for raw in stream:
                    last_output[0] = time.monotonic()
                    line = raw.rstrip("\n")
                    if stream_name == "stdout":
                        event = _parse_event(line)
                        payload = (
                            {"type": "event", "event": event}
                            if event is not None
                            else {"type": "log", "stream": "stdout", "line": line}
                        )
                    else:
                        payload = {"type": "log", "stream": "stderr", "line": line}
                    try:
                        send(payload)
                    except ClientGone:
                        fail()
                        return
            except (ValueError, OSError):
                return
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        def monitor() -> None:
            next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
            while not stop.wait(0.2):
                now = time.monotonic()
                if now - last_output[0] > self.config.idle_timeout:
                    timed_out[0] = True
                    _kill(proc)
                    return
                if now >= next_heartbeat:
                    next_heartbeat = now + HEARTBEAT_INTERVAL
                    try:
                        send({"type": "heartbeat"})
                    except ClientGone:
                        fail()
                        return

        threads = [
            threading.Thread(target=read, args=(proc.stdout, "stdout"), daemon=True),
            threading.Thread(target=read, args=(proc.stderr, "stderr"), daemon=True),
            threading.Thread(target=monitor, daemon=True),
        ]
        for thread in threads:
            thread.start()
        try:
            proc.wait()
        finally:
            stop.set()
            # Readers must drain all output before "exit" is sent
            for thread in threads[:2]:
                thread.join(timeout=DRAIN_TIMEOUT)
            threads[-1].join(timeout=5)

    def _terminate(self, proc: subprocess.Popen) -> None:
        _kill(proc)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    def _remove_container(self, name: str) -> None:
        try:
            subprocess.run(
                [self.config.docker_bin, "rm", "-f", name],
                env=_subprocess_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except Exception:
            logging.warning("failed to remove sandbox container")


def reap_stale_sandboxes(config: Config) -> None:
    env = _subprocess_env()
    try:
        result = subprocess.run(
            [config.docker_bin, "ps", "-aq", "--filter", "label=plane.agent.sandbox=1"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except Exception:
        logging.warning("could not list stale sandbox containers")
        return

    if result.returncode != 0:
        logging.warning("docker ps failed while reaping stale sandboxes")
        return

    for container_id in result.stdout.split():
        try:
            subprocess.run(
                [config.docker_bin, "rm", "-f", container_id],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
                check=False,
            )
        except Exception:
            logging.warning("failed to remove a stale sandbox container")
