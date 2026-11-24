# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Client for the internal agent runner service.

The runner is an internal container on Plane's own network, so requests go
through ``requests`` directly: the SSRF guard is for user-provided URLs, and the
sandbox API key is injected by the runner, never sent from here.
"""

# Python imports
import json
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal

# Third party imports
import requests

# Module imports
from plane.db.models import OPENCODE_BASE_URL, AIAgent
from plane.license.utils.encryption import decrypt_data
from plane.utils.agent_runtime import AgentRuntimeError

DEFAULT_RUNNER_URL = "http://agent-runner:8787"
# The runner sends a heartbeat every 15s, so a quiet stream is not a dead one
PROGRESS_INTERVAL = 15
KEEP_STDERR_LINES = 20
CONNECT_TIMEOUT = 10
READ_TIMEOUT = 60

USAGE_KEYS = ("input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens", "cache_write_tokens")


class AgentRunnerError(AgentRuntimeError):
    pass


@dataclass(frozen=True)
class SandboxCall:
    agent: AIAgent
    model: str
    effort: str
    prompt: str
    title: str
    session_id: str
    sandbox_key: str
    # Both paths are relative to the agent workspace root
    workspace: str
    sessions: str
    read_only: bool
    allowed_hosts: tuple = ()
    websearch: bool = False
    webfetch: bool = False
    deny_edit: bool = False
    cache: str = ""
    skills: str = ""
    # [{"name", "url", "headers"}]: headers carry secrets and never reach the trace
    mcp_servers: tuple = ()


@dataclass
class SandboxTrace:
    request: dict = field(default_factory=dict)
    text: str = ""
    reasoning: str = ""
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    cost: Decimal | None = None
    stop_reason: str = ""


def _error_text(error) -> str:
    if isinstance(error, dict):
        data = error.get("data") if isinstance(error.get("data"), dict) else {}
        message = error.get("message") or data.get("message") or error.get("name")
        return str(message or "The agent failed.")
    return str(error or "The agent failed.")


def _tokens(tokens) -> dict | None:
    if not isinstance(tokens, dict):
        return None
    cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
    return {
        "input_tokens": tokens.get("input") or 0,
        "output_tokens": tokens.get("output") or 0,
        "reasoning_tokens": tokens.get("reasoning") or 0,
        "cache_read_tokens": cache.get("read") or 0,
        "cache_write_tokens": cache.get("write") or 0,
    }


def _record_step_finish(trace: SandboxTrace, part: dict) -> None:
    usage = _tokens(part.get("tokens"))
    if usage is not None:
        for key in USAGE_KEYS:
            trace.usage[key] = trace.usage.get(key, 0) + int(usage[key])
    cost = part.get("cost")
    if cost is not None:
        try:
            trace.cost = (trace.cost or Decimal(0)) + Decimal(str(cost))
        except Exception:
            pass
    if part.get("reason"):
        trace.stop_reason = str(part["reason"])


def run_sandbox(call: SandboxCall, trace: SandboxTrace, on_progress=None) -> str:
    """Run one OpenCode sandbox and return its final answer."""
    token = os.environ.get("AGENT_RUNNER_TOKEN")
    if not token:
        raise AgentRunnerError("The agent runner is not configured.")

    api_key = decrypt_data(call.agent.api_key)
    if not api_key:
        raise AgentRunnerError(f"No API key configured for {call.agent.name}.")

    # Traced before the send: only the body, never the key or the headers
    trace.request = {
        "prompt": call.prompt,
        "model": call.model,
        "effort": call.effort,
        "session_id": call.session_id,
        "read_only": call.read_only,
        "title": call.title,
    }
    body = {
        "sandbox_key": call.sandbox_key,
        "workspace": call.workspace,
        "sessions": call.sessions,
        "read_only": call.read_only,
        "session_id": call.session_id,
        "model": call.model,
        "effort": call.effort or "",
        "prompt": call.prompt,
        "title": call.title,
        "base_url": call.agent.base_url or OPENCODE_BASE_URL,
        "api_key": api_key,
        "allowed_hosts": list(call.allowed_hosts),
        "websearch": call.websearch,
        "webfetch": call.webfetch,
        "deny_edit": call.deny_edit,
        "cache": call.cache,
        "skills": call.skills,
        "mcp_servers": list(call.mcp_servers),
    }
    url = (os.environ.get("AGENT_RUNNER_URL") or DEFAULT_RUNNER_URL).rstrip("/")

    response = None
    last_progress = 0.0
    stderr_lines = []
    step_parts = {}
    step_order = []
    last_step_text = ""
    reasoning_parts = {}
    reasoning_order = []
    tool_calls = {}
    tool_order = []
    saw_exit = False
    exit_code = 0
    timed_out = False

    def flush_step():
        nonlocal last_step_text
        text = "".join(step_parts[part_id] for part_id in step_order)
        if text.strip():
            last_step_text = text
        step_parts.clear()
        step_order.clear()
        trace.text = last_step_text

    def handle_event(event):
        nonlocal exit_code, timed_out
        kind = event.get("type")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}

        if kind == "step_start":
            flush_step()
        elif kind in ("text", "reasoning", "tool_use") and not part:
            return
        elif kind == "text":
            part_id = str(part.get("id") or "")
            if not part_id:
                return
            if part_id not in step_parts:
                step_order.append(part_id)
            step_parts[part_id] = str(part.get("text") or "")
            trace.text = "".join(step_parts[part_id] for part_id in step_order)
        elif kind == "reasoning":
            part_id = str(part.get("id") or "")
            if not part_id:
                return
            if part_id not in reasoning_parts:
                reasoning_order.append(part_id)
            reasoning_parts[part_id] = str(part.get("text") or "")
            trace.reasoning = "\n\n".join(reasoning_parts[part_id] for part_id in reasoning_order)
        elif kind == "tool_use":
            part_id = str(part.get("id") or "")
            if not part_id:
                return
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            if part_id not in tool_calls:
                tool_order.append(part_id)
            tool_calls[part_id] = {
                "tool": part.get("tool") or "",
                "status": state.get("status") or "",
                "title": state.get("title") or "",
                "input": state.get("input") if state.get("input") is not None else {},
                "output": state.get("output") or "",
                "error": state.get("error") or "",
            }
            trace.tool_calls = [tool_calls[key] for key in tool_order]
        elif kind == "step_finish":
            flush_step()
            _record_step_finish(trace, part)
        elif kind == "error":
            raise AgentRunnerError(_error_text(event.get("error")))

    try:
        try:
            response = requests.post(
                f"{url}/v1/sandboxes",
                headers={"Authorization": f"Bearer {token}", "content-type": "application/json"},
                json=body,
                stream=True,
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            )
        except requests.RequestException as e:
            raise AgentRunnerError(f"Could not reach the agent runner: {e}") from e

        if response.status_code != 200:
            message = ""
            try:
                payload = response.json()
                if isinstance(payload, dict):
                    message = str(payload.get("error") or "")
            except Exception:
                message = ""
            raise AgentRunnerError(message or f"The agent runner returned HTTP {response.status_code}.")

        try:
            for raw in response.iter_lines():
                now = time.monotonic()
                if on_progress is not None and now - last_progress >= PROGRESS_INTERVAL:
                    last_progress = now
                    # A raising callback stops the read, and the runner removes the container
                    on_progress()
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue

                kind = payload.get("type")
                if kind == "log":
                    if payload.get("stream") == "stderr":
                        stderr_lines = (stderr_lines + [str(payload.get("line") or "")])[-KEEP_STDERR_LINES:]
                elif kind == "event":
                    event = payload.get("event")
                    if isinstance(event, dict):
                        handle_event(event)
                elif kind == "exit":
                    saw_exit = True
                    exit_code = payload.get("code")
                    timed_out = bool(payload.get("timed_out"))
        except requests.RequestException as e:
            # Never echo the body: it carries the provider key
            raise AgentRunnerError(f"The agent runner stream failed: {e}") from e

        if not saw_exit:
            raise AgentRunnerError("The agent runner closed the stream before the sandbox exited.")
        if timed_out:
            raise AgentRunnerError("The agent stopped producing output.")
        if exit_code not in (0, None):
            detail = "\n".join(stderr_lines).strip()
            message = f"The agent failed (exit code {exit_code})."
            raise AgentRunnerError(f"{message}\n{detail}" if detail else message)
        if trace.stop_reason == "length":
            raise AgentRunnerError("The model stopped before finishing its answer.")
    finally:
        if response is not None:
            response.close()

    return trace.text.strip()
