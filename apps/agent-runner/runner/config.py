# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Environment-driven configuration for the agent runner."""

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when the environment does not describe a usable runner."""


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc


@dataclass(frozen=True)
class Config:
    token: str
    workspace_root: str
    workspace_host_root: str
    sandbox_image: str = "plane-agent-sandbox:2.2.6"
    network: str = "plane-agent-sandbox"
    proxy_host: str = "agent-runner"
    api_port: int = 8787
    proxy_port: int = 8788
    egress_port: int = 8789
    cpus: str = "1"
    memory: str = "3g"
    pids: int = 512
    tmp_size: str = "1g"
    max_sandboxes: int = 2
    idle_timeout: int = 900
    docker_bin: str = "docker"

    @classmethod
    def from_env(cls) -> "Config":
        token = os.environ.get("AGENT_RUNNER_TOKEN", "")
        if len(token) < 32:
            raise ConfigError("AGENT_RUNNER_TOKEN must be at least 32 characters")

        host_root = os.environ.get("AGENT_WORKSPACE_HOST_ROOT", "")
        if not host_root or not os.path.isabs(host_root):
            raise ConfigError("AGENT_WORKSPACE_HOST_ROOT must be an absolute path")

        return cls(
            token=token,
            workspace_root=os.environ.get("AGENT_WORKSPACE_ROOT", "/agent-workspaces"),
            workspace_host_root=host_root,
            sandbox_image=os.environ.get("AGENT_SANDBOX_IMAGE", "plane-agent-sandbox:2.2.6"),
            network=os.environ.get("AGENT_SANDBOX_NETWORK", "plane-agent-sandbox"),
            proxy_host=os.environ.get("AGENT_RUNNER_PROXY_HOST", "agent-runner"),
            api_port=_int_env("AGENT_RUNNER_API_PORT", 8787),
            proxy_port=_int_env("AGENT_RUNNER_PROXY_PORT", 8788),
            egress_port=_int_env("AGENT_RUNNER_EGRESS_PORT", 8789),
            cpus=os.environ.get("AGENT_SANDBOX_CPUS", "1"),
            memory=os.environ.get("AGENT_SANDBOX_MEMORY", "3g"),
            pids=_int_env("AGENT_SANDBOX_PIDS", 512),
            tmp_size=os.environ.get("AGENT_SANDBOX_TMP_SIZE", "1g"),
            max_sandboxes=_int_env("AGENT_MAX_SANDBOXES", 2),
            idle_timeout=_int_env("AGENT_IDLE_TIMEOUT", 900),
            docker_bin=os.environ.get("AGENT_DOCKER_BIN", "docker"),
        )
