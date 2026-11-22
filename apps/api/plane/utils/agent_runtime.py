# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared model invocation used by the agent pipeline."""

# Python imports
import json

# Third party imports
import requests
from openai import OpenAI

# Module imports
from plane.db.models import AgentProvider, AIAgent
from plane.license.utils.encryption import decrypt_data
from plane.utils.exception_logger import log_exception

ANTHROPIC_VERSION = "2023-06-01"
REQUEST_TIMEOUT = 180


class AgentRuntimeError(Exception):
    pass


def agent_headers(agent: AIAgent) -> dict:
    """OpenCode Go requires a stable session id and a client user agent."""
    if agent.base_url and "opencode.ai" in agent.base_url:
        return {"x-opencode-session": str(agent.id), "user-agent": "plane-ai-agent/1.0"}
    return {}


def provider_error(response) -> str:
    try:
        payload = response.json()
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        return str(message or payload.get("message") or f"HTTP {response.status_code}")[:300]
    except Exception:
        return f"HTTP {response.status_code}"


def invoke_agent(agent: AIAgent, messages: list, max_tokens: int = 4096, effort_override: str | None = None) -> str:
    """Send a chat conversation to the agent's provider and return the text answer."""
    api_key = decrypt_data(agent.api_key)
    if not api_key:
        raise AgentRuntimeError(f"No API key configured for {agent.name}.")

    effort = effort_override if effort_override is not None else agent.reasoning_effort

    try:
        if agent.provider == AgentProvider.ANTHROPIC:
            base_url = (agent.base_url or "https://api.anthropic.com").rstrip("/")
            payload = {"model": agent.model, "max_tokens": max_tokens, "messages": messages}
            if agent.system_prompt:
                payload["system"] = agent.system_prompt
            if effort:
                payload["output_config"] = {"effort": effort}
            response = requests.post(
                f"{base_url}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                    **agent_headers(agent),
                },
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code != 200:
                raise AgentRuntimeError(f"{agent.name} failed: {provider_error(response)}")
            blocks = response.json().get("content", [])
            return "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text").strip()

        client = OpenAI(
            api_key=api_key,
            base_url=agent.base_url or None,
            timeout=REQUEST_TIMEOUT,
            default_headers=agent_headers(agent),
        )
        chat = ([{"role": "system", "content": agent.system_prompt}] if agent.system_prompt else []) + messages
        kwargs = {"model": agent.model, "messages": chat, "max_tokens": max_tokens}
        if effort:
            kwargs["reasoning_effort"] = effort
        completion = client.chat.completions.create(**kwargs)
        return (completion.choices[0].message.content or "").strip()
    except AgentRuntimeError:
        raise
    except Exception as e:
        log_exception(e)
        raise AgentRuntimeError(f"{agent.name} failed: {e}") from e


def extract_json(text: str):
    """Parse a JSON object out of a model answer, tolerating code fences and prose."""
    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        return json.loads(cleaned[start : end + 1])
    except (ValueError, TypeError):
        return None
