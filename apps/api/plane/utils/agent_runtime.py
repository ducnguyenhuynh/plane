# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared provider helpers used by the agent pipeline.

Only OpenCode inference endpoints are supported. The base URL is user-provided,
so every direct call goes through :func:`plane.utils.url_security.pinned_fetch`.
"""

# Python imports
import json
import re
import time

# Third party imports
import requests
from django.conf import settings
from django.utils.html import escape

# Module imports
from plane.db.models import OPENCODE_BASE_URL, AgentProvider, AIAgent
from plane.license.utils.encryption import decrypt_data
from plane.utils.exception_logger import log_exception
from plane.utils.url_security import pinned_fetch

# Non-streaming read timeout
REQUEST_TIMEOUT = 180

# Transient provider failures worth retrying; anything else is a real answer
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
RETRY_DELAYS = (1, 3)
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER = 30


class AgentRuntimeError(Exception):
    pass


def agent_headers(agent: AIAgent) -> dict:
    """OpenCode Go requires a stable session id and a client user agent."""
    if agent.base_url and "opencode.ai" in agent.base_url:
        return {"x-opencode-session": str(agent.id), "user-agent": "plane-ai-agent/1.0"}
    return {}


def provider_url(agent: AIAgent) -> str:
    return f"{(agent.base_url or OPENCODE_BASE_URL).rstrip('/')}/chat/completions"


def provider_headers(agent: AIAgent, api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "content-type": "application/json",
        **agent_headers(agent),
    }


def provider_payload(
    agent: AIAgent,
    messages: list,
    max_tokens: int,
    effort: str | None = None,
    model: str | None = None,
) -> dict:
    chat = ([{"role": "system", "content": agent.system_prompt}] if agent.system_prompt else []) + messages
    payload = {"model": model or agent.model, "messages": chat, "max_tokens": max_tokens}
    if effort:
        payload["reasoning_effort"] = effort
    return payload


def provider_error(response) -> str:
    try:
        payload = response.json()
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        return str(message or payload.get("message") or f"HTTP {response.status_code}")[:300]
    except Exception:
        return f"HTTP {response.status_code}"


def retry_after_seconds(response) -> int | None:
    value = (response.headers or {}).get("Retry-After")
    if not value:
        return None
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def provider_post(agent: AIAgent, api_key: str, payload: dict, timeout=REQUEST_TIMEOUT):
    """POST to the agent's provider with bounded retries on transient failures."""
    for attempt in range(MAX_ATTEMPTS):
        wait = None
        try:
            response = pinned_fetch(
                "POST",
                provider_url(agent),
                allowed_ips=settings.WEBHOOK_ALLOWED_IPS,
                allowed_hosts=settings.WEBHOOK_ALLOWED_HOSTS,
                headers=provider_headers(agent, api_key),
                json=payload,
                timeout=timeout,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            if attempt == MAX_ATTEMPTS - 1:
                raise AgentRuntimeError(f"Could not reach the provider: {e}") from e
            wait = RETRY_DELAYS[attempt]
        else:
            if response.status_code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS - 1:
                return response
            wait = retry_after_seconds(response)
            if wait is None:
                wait = RETRY_DELAYS[attempt]
            response.close()
        time.sleep(min(wait, MAX_RETRY_AFTER))
    raise AgentRuntimeError("The provider could not be reached.")


def check_agent_connection(agent: AIAgent) -> tuple[bool, str]:
    """Send one minimal request to the provider so bad credentials surface in the UI, not later."""
    if agent.provider != AgentProvider.OPENCODE:
        return False, "Only the OpenCode provider is supported."

    api_key = decrypt_data(agent.api_key)
    if not api_key:
        return False, "No API key configured for this agent."

    payload = provider_payload(
        agent,
        [{"role": "user", "content": "ping"}],
        16,
        agent.reasoning_effort or None,
    )
    try:
        response = provider_post(agent, api_key, payload, timeout=15)
    except AgentRuntimeError as e:
        log_exception(e)
        return False, "Could not reach the provider. Check the base URL and network access."
    except Exception as e:
        log_exception(e)
        return False, "The connection test failed. Check the API logs for details."

    if response.status_code == 200:
        return True, "Connection successful."
    if response.status_code in (401, 403):
        return False, "Authentication failed. Check the API key."
    if response.status_code == 404:
        return False, "Model not found. Check the model name or base URL."
    detail = provider_error(response)
    message = f"The provider rejected the request (HTTP {response.status_code})."
    if detail and detail != f"HTTP {response.status_code}":
        message = f"{message} {detail}"
    return False, message


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


def markdown_to_html(text: str) -> str:
    """Render the small Markdown subset agent comments use: fences, paragraphs, line breaks.

    Everything is escaped before any markup is added, so model output can never
    inject HTML.
    """
    escaped = escape(text or "")
    parts = re.split(r"```[^\n]*\n(.*?)```", escaped, flags=re.S)
    html = []
    for index, part in enumerate(parts):
        if index % 2:
            html.append(f"<pre><code>{part.rstrip(chr(10))}</code></pre>")
            continue
        for block in re.split(r"\n\s*\n", part.strip()):
            if block.strip():
                html.append(f"<p>{block.strip().replace(chr(10), '<br>')}</p>")
    return "".join(html)
