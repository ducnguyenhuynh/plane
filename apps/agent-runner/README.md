# Agent runner

Docker-only service that runs the `opencode` CLI in a locked-down sandbox on
behalf of Plane's Celery worker. It is the only process that talks to the
Docker socket.

## Security model

- The sandbox never receives the real provider API key, a GitHub token, or any
  Plane secret. The CLI is configured with `apiKey: "sandbox"` and talks to the
  runner's LLM proxy at a per-run random URL; the proxy injects the real key
  upstream and drops the sandbox's own `Authorization` header.
- Sandboxes run with `--read-only`, `--cap-drop ALL`,
  `no-new-privileges`, no network privileges and a dedicated bridge network;
  the Docker socket is never mounted and `--privileged` is never used.
- Workspaces and session directories are validated as relative paths inside
  `AGENT_WORKSPACE_ROOT`; symlinked paths are rejected.
- The API requires `Authorization: Bearer <AGENT_RUNNER_TOKEN>`.
- Secrets, tokens and request bodies are never logged.

## Configuration

| Variable                    | Default                     | Meaning                              |
| --------------------------- | --------------------------- | ------------------------------------ |
| `AGENT_RUNNER_TOKEN`        | required, >= 32 chars       | bearer token for the API             |
| `AGENT_WORKSPACE_ROOT`      | `/agent-workspaces`         | workspace root inside the runner     |
| `AGENT_WORKSPACE_HOST_ROOT` | required, absolute          | same directory on the Docker host    |
| `AGENT_SANDBOX_IMAGE`       | `plane-agent-sandbox:2.2.6` | sandbox image                        |
| `AGENT_SANDBOX_NETWORK`     | `plane-agent-sandbox`       | sandbox network                      |
| `AGENT_RUNNER_PROXY_HOST`   | `agent-runner`              | host the sandbox uses for the proxy  |
| `AGENT_RUNNER_API_PORT`     | `8787`                      | API port                             |
| `AGENT_RUNNER_PROXY_PORT`   | `8788`                      | proxy port                           |
| `AGENT_SANDBOX_CPUS`        | `1`                         | CPU limit                            |
| `AGENT_SANDBOX_MEMORY`      | `3g`                        | memory limit (also swap)             |
| `AGENT_SANDBOX_PIDS`        | `512`                       | PID limit                            |
| `AGENT_SANDBOX_TMP_SIZE`    | `1g`                        | `/tmp` tmpfs size                    |
| `AGENT_MAX_SANDBOXES`       | `2`                         | concurrent sandboxes                 |
| `AGENT_IDLE_TIMEOUT`        | `900`                       | seconds without output before a kill |
| `AGENT_DOCKER_BIN`          | `docker`                    | docker CLI binary                    |

## Network

Sandboxes have no direct internet access. `HTTP(S)_PROXY` points at the egress
proxy (`AGENT_RUNNER_EGRESS_PORT`, default 8789), which only tunnels CONNECT to
port 443 of hosts the run allows. Every host is resolved first and refused
unless all of its addresses are public, and the per-sandbox token
authenticates each tunnel.

## Skills

Enabled project skills are written as root-owned `SKILL.md` files and mounted
read-only at `/home/agent/.plane-skills`.

## MCP

Enabled remote MCP servers are exposed to the sandbox at
`/mcp/<sandbox token>/<name>` on the LLM proxy. Stored headers are injected
there, so MCP secrets never enter the sandbox.

## Disk limit

`/workspace` has no quota of its own, so `AGENT_WORKSPACE_HOST_ROOT` must be its own
fixed-size filesystem. Otherwise an agent can fill the host disk. A 20 GB ext4
loopback file:

```sh
sudo mkdir -p /srv/plane-agent-workspaces
sudo dd if=/dev/zero of=/srv/plane-agent-workspaces.img bs=1M count=20480
sudo mkfs.ext4 /srv/plane-agent-workspaces.img
sudo mount -o loop,defaults /srv/plane-agent-workspaces.img /srv/plane-agent-workspaces
```

Persist it across reboots with this `/etc/fstab` line:

```fstab
/srv/plane-agent-workspaces.img /srv/plane-agent-workspaces ext4 loop,defaults,nofail,x-systemd.before=docker.service 0 0
```

## Build

```sh
docker build -f sandbox.Dockerfile -t plane-agent-sandbox:2.2.6 .
docker build -t plane-agent-runner:latest .
```

## Run

```sh
docker run --rm \
  -e AGENT_RUNNER_TOKEN=... \
  -e AGENT_WORKSPACE_HOST_ROOT=/srv/agent-workspaces \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v /srv/agent-workspaces:/agent-workspaces \
  -p 8787:8787 -p 8788:8788 \
  plane-agent-runner:latest
```

## Tests

```sh
cd apps/agent-runner && ../api/.venv/bin/python -m unittest discover -s tests -t .
```
