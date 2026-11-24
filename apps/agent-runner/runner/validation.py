# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.
"""Request validation for the agent runner API.

Error messages are deliberately generic: request bodies can contain provider
API keys and prompts, and neither may end up in a client-visible error.
"""

import os
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .config import Config

SANDBOX_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
WORKSPACE_RE = re.compile(r"^runs/[A-Za-z0-9_-]+/(tasks|review|plan)/[A-Za-z0-9_-]+$")
SESSIONS_RE = re.compile(r"^sessions/[A-Za-z0-9_-]+$")
SESSION_ID_RE = re.compile(r"^ses_[A-Za-z0-9_]{1,76}$")
MODEL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
# Every segment starts with a non-dot character, which rejects "." , ".." and
# any percent-encoded variant without a separate check.
BASE_PATH_RE = re.compile(r"/inference/go(?:/[A-Za-z0-9_-][A-Za-z0-9._-]*)+")

EFFORTS = frozenset({"", "none", "minimal", "low", "medium", "high", "xhigh", "max"})

HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
FORBIDDEN_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})
CACHE_RE = re.compile(r"^caches/[A-Za-z0-9_-]+$")
SKILLS_RE = re.compile(r"^runs/[A-Za-z0-9_-]+/skills$")
MAX_ALLOWED_HOSTS = 200
MAX_MCP_SERVERS = 20


class ValidationError(ValueError):
    """A request failed validation. The message is safe to return to clients."""


@dataclass(frozen=True)
class McpServer:
    """A remote MCP server, already parsed so the proxy never re-parses user input."""

    name: str
    host: str
    port: int
    path: str
    headers: tuple


@dataclass(frozen=True)
class SandboxRequest:
    sandbox_key: str
    workspace: str
    sessions: str
    read_only: bool
    session_id: str
    model: str
    effort: str
    prompt: str
    title: str
    base_url: str
    api_key: str
    allowed_hosts: tuple = ()
    websearch: bool = False
    webfetch: bool = False
    deny_edit: bool = False
    cache: str = ""
    skills: str = ""
    mcp_servers: tuple = ()


def _text(body: dict, field: str, *, allow_empty: bool = False) -> str:
    value = body.get(field)
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise ValidationError(f"invalid {field}")
    return value


def _directory(config: Config, field: str, value: str) -> None:
    # Realpath the root too: tests and deployments may live behind a symlinked
    # parent (macOS /var), while a symlink *inside* the root is rejected.
    root = os.path.realpath(config.workspace_root)
    joined = os.path.join(root, value)
    if os.path.realpath(joined) != joined or not os.path.isdir(joined):
        raise ValidationError(f"invalid {field}")


def _optional_directory(config: Config, field: str, value, pattern: re.Pattern) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValidationError(f"invalid {field}")
    _directory(config, field, value)
    return value


def _boolean(body: dict, field: str) -> bool:
    value = body.get(field, False)
    if not isinstance(value, bool):
        raise ValidationError(f"invalid {field}")
    return value


def _allowed_hosts(body: dict) -> tuple:
    value = body.get("allowed_hosts", [])
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > MAX_ALLOWED_HOSTS:
        raise ValidationError("invalid allowed_hosts")
    hosts = []
    for item in value:
        if not isinstance(item, str) or not HOSTNAME_RE.fullmatch(item.lower()):
            raise ValidationError("invalid allowed_hosts")
        host = item.lower()
        if host not in hosts:
            hosts.append(host)
    return tuple(hosts)


def _mcp_servers(body: dict) -> tuple:
    value = body.get("mcp_servers", [])
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > MAX_MCP_SERVERS:
        raise ValidationError("invalid mcp_servers")

    servers = []
    names = set()
    for item in value:
        if not isinstance(item, dict):
            raise ValidationError("invalid mcp_servers")
        name = item.get("name")
        if not isinstance(name, str) or len(name) > 40 or not SLUG_RE.fullmatch(name) or name in names:
            raise ValidationError("invalid mcp_servers")
        names.add(name)

        url = item.get("url")
        if not isinstance(url, str):
            raise ValidationError("invalid mcp_servers")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise ValidationError("invalid mcp_servers") from exc
        if (
            parsed.scheme != "https"
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.hostname
            or not HOSTNAME_RE.fullmatch(parsed.hostname)
        ):
            raise ValidationError("invalid mcp_servers")

        headers = item.get("headers") or {}
        if not isinstance(headers, dict):
            raise ValidationError("invalid mcp_servers")
        pairs = []
        for key, header_value in headers.items():
            if not isinstance(key, str) or not HEADER_NAME_RE.fullmatch(key) or key.lower() in FORBIDDEN_HEADERS:
                raise ValidationError("invalid mcp_servers")
            if not isinstance(header_value, str) or "\r" in header_value or "\n" in header_value:
                raise ValidationError("invalid mcp_servers")
            pairs.append((key, header_value))
        servers.append(
            McpServer(
                name=name,
                host=parsed.hostname,
                port=443,
                path=parsed.path or "/",
                headers=tuple(pairs),
            )
        )
    return tuple(servers)


def _base_url(body: dict) -> str:
    value = _text(body, "base_url")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("invalid base_url") from exc

    if parsed.scheme != "https" or parsed.hostname != "opencode.ai":
        raise ValidationError("invalid base_url")
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError("invalid base_url")
    if parsed.query or parsed.fragment:
        raise ValidationError("invalid base_url")
    if port not in (None, 443):
        raise ValidationError("invalid base_url")
    path = parsed.path[:-1] if parsed.path.endswith("/") else parsed.path
    if not BASE_PATH_RE.fullmatch(path):
        raise ValidationError("invalid base_url")

    return value[:-1] if value.endswith("/") else value


def validate_request(body: dict, config: Config) -> SandboxRequest:
    if not isinstance(body, dict):
        raise ValidationError("invalid request body")

    sandbox_key = _text(body, "sandbox_key")
    if not SANDBOX_KEY_RE.match(sandbox_key):
        raise ValidationError("invalid sandbox_key")

    workspace = _text(body, "workspace")
    if not WORKSPACE_RE.match(workspace):
        raise ValidationError("invalid workspace")
    _directory(config, "workspace", workspace)

    sessions = _text(body, "sessions")
    if not SESSIONS_RE.match(sessions):
        raise ValidationError("invalid sessions")
    _directory(config, "sessions", sessions)

    read_only = body.get("read_only", False)
    if not isinstance(read_only, bool):
        raise ValidationError("invalid read_only")

    session_id = _text(body, "session_id")
    if not SESSION_ID_RE.match(session_id):
        raise ValidationError("invalid session_id")

    model = _text(body, "model")
    if not MODEL_RE.match(model):
        raise ValidationError("invalid model")

    effort = body.get("effort", "")
    if not isinstance(effort, str) or effort not in EFFORTS:
        raise ValidationError("invalid effort")

    prompt = _text(body, "prompt")

    title = body.get("title", "")
    if not isinstance(title, str):
        raise ValidationError("invalid title")
    title = CONTROL_RE.sub("", title)[:120]

    base_url = _base_url(body)
    api_key = _text(body, "api_key")

    return SandboxRequest(
        sandbox_key=sandbox_key,
        workspace=workspace,
        sessions=sessions,
        read_only=read_only,
        session_id=session_id,
        model=model,
        effort=effort,
        prompt=prompt,
        title=title,
        base_url=base_url,
        api_key=api_key,
        allowed_hosts=_allowed_hosts(body),
        websearch=_boolean(body, "websearch"),
        webfetch=_boolean(body, "webfetch"),
        deny_edit=_boolean(body, "deny_edit"),
        cache=_optional_directory(config, "cache", body.get("cache"), CACHE_RE),
        skills=_optional_directory(config, "skills", body.get("skills"), SKILLS_RE),
        mcp_servers=_mcp_servers(body),
    )
