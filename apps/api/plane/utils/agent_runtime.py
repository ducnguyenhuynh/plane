# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Shared model invocation used by the agent pipeline.

Every outbound call goes through :func:`plane.utils.url_security.pinned_fetch`
because the base URL is user-provided (SSRF).
"""

# Python imports
import copy
import json
import logging
import re
import time
from dataclasses import dataclass, field

# Third party imports
import requests
from django.conf import settings
from django.utils.html import escape

# Module imports
from plane.db.models import AgentProvider, AIAgent
from plane.license.utils.encryption import decrypt_data
from plane.utils.exception_logger import log_exception
from plane.utils.url_security import pinned_fetch

ANTHROPIC_VERSION = "2023-06-01"
# Reasoning models spend small budgets thinking; streaming keeps a long answer from timing out
ANTHROPIC_MAX_OUTPUT_TOKENS = 128000
OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS = 384000
FALLBACK_MAX_OUTPUT_TOKENS = 64000
STREAM_CONNECT_TIMEOUT = 15
# Non-streaming read timeout; for streams it is the idle read timeout between chunks
REQUEST_TIMEOUT = 180
DEFAULT_ANTHROPIC_URL = "https://api.anthropic.com"
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
# A reported cap below this is a digit from a model name, not an output budget
MIN_OUTPUT_CAP = 1024

# Transient provider failures worth retrying; anything else is a real answer
RETRY_STATUSES = {429, 500, 502, 503, 504, 529}
RETRY_DELAYS = (1, 3)
MAX_ATTEMPTS = 3
MAX_RETRY_AFTER = 30
# Resends after the provider rejects an unsupported request field
ADJUSTED_RESENDS = 3
# 422 is what some OpenAI-compatible gateways return for an invalid parameter
PARAMETER_ERROR_STATUSES = {400, 422}
# How often a streaming answer reports progress to its caller
PROGRESS_INTERVAL = 15

TRUNCATION_NOTICE = "\n\n> Output was truncated at the token limit."

logger = logging.getLogger("plane.worker")


class AgentRuntimeError(Exception):
    pass


@dataclass
class AgentCallTrace:
    """What one model call sent and received, so a run can be traced later."""

    request: dict = field(default_factory=dict)
    max_tokens: int | None = None
    text_parts: list = field(default_factory=list)
    reasoning_parts: list = field(default_factory=list)
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)

    @property
    def text(self) -> str:
        return "".join(self.text_parts)

    @property
    def reasoning(self) -> str:
        return "".join(self.reasoning_parts)


def agent_headers(agent: AIAgent) -> dict:
    """OpenCode Go requires a stable session id and a client user agent."""
    if agent.base_url and "opencode.ai" in agent.base_url:
        return {"x-opencode-session": str(agent.id), "user-agent": "plane-ai-agent/1.0"}
    return {}


def provider_url(agent: AIAgent) -> str:
    if agent.provider == AgentProvider.ANTHROPIC:
        return f"{(agent.base_url or DEFAULT_ANTHROPIC_URL).rstrip('/')}/v1/messages"
    return f"{(agent.base_url or DEFAULT_OPENAI_URL).rstrip('/')}/chat/completions"


def provider_headers(agent: AIAgent, api_key: str) -> dict:
    if agent.provider == AgentProvider.ANTHROPIC:
        return {
            "x-api-key": api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            **agent_headers(agent),
        }
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
    stream: bool = False,
) -> dict:
    if agent.provider == AgentProvider.ANTHROPIC:
        payload = {"model": model or agent.model, "max_tokens": max_tokens, "messages": messages}
        if agent.system_prompt:
            payload["system"] = agent.system_prompt
        if effort:
            payload["output_config"] = {"effort": effort}
        if stream:
            payload["stream"] = True
        return payload

    chat = ([{"role": "system", "content": agent.system_prompt}] if agent.system_prompt else []) + messages
    payload = {"model": model or agent.model, "messages": chat, "max_tokens": max_tokens}
    if effort:
        payload["reasoning_effort"] = effort
    if stream:
        payload["stream"] = True
        # Without this gateways drop the token counts from the stream
        payload["stream_options"] = {"include_usage": True}
    return payload


def provider_error(response) -> str:
    try:
        payload = response.json()
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        return str(message or payload.get("message") or f"HTTP {response.status_code}")[:300]
    except Exception:
        return f"HTTP {response.status_code}"


def output_cap_from_error(message: str, requested: int) -> int | None:
    """The output cap a provider reports in its 400, if any.

    "max_tokens: 128000 > 64000, which is the maximum" gives 64000 and
    "valid range of max_tokens is [1, 8192]" gives 8192. Digits inside a model
    name ("qwen4-max") are not caps, so a minimum is enforced.
    """
    caps = [int(token) for token in re.findall(r"(?<![\w.-])\d+(?![\w-])", message or "")]
    caps = [value for value in caps if MIN_OUTPUT_CAP <= value < requested]
    return max(caps) if caps else None


def retry_after_seconds(response) -> int | None:
    value = (response.headers or {}).get("Retry-After")
    if not value:
        return None
    try:
        return max(0, int(str(value).strip()))
    except (TypeError, ValueError):
        return None


def provider_post(agent: AIAgent, api_key: str, payload: dict, timeout=REQUEST_TIMEOUT, stream: bool = False):
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
                **({"stream": True} if stream else {}),
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


def stream_error_message(event: dict) -> str:
    error = event.get("error")
    message = error.get("message") if isinstance(error, dict) else error
    return str(message or "stream error")


def read_stream(agent: AIAgent, response, trace: AgentCallTrace | None = None, on_progress=None) -> tuple[str, bool]:
    """Read a server-sent event stream into ``(text, truncated)``.

    A stream that ends without a stop reason is an error: a cut-off answer must
    never be returned as if it were complete.
    """
    anthropic = agent.provider == AgentProvider.ANTHROPIC
    parts = []
    stop_reason = None
    last_progress = 0.0

    def progress():
        # Throttled per stream; a raising callback stops the read and the spending
        nonlocal last_progress
        if on_progress is None:
            return
        now = time.monotonic()
        if now - last_progress < PROGRESS_INTERVAL:
            return
        last_progress = now
        on_progress()

    def keep_text(text):
        parts.append(text)
        if trace is not None:
            trace.text_parts.append(text)

    def keep_reasoning(text):
        if trace is not None and text:
            trace.reasoning_parts.append(text)

    try:
        for raw in response.iter_lines():
            if not raw:
                continue
            progress()
            line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
            if not line.startswith("data:"):
                continue
            data = line[len("data:") :].strip()
            if not anthropic and data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("error") or event.get("type") == "error":
                raise AgentRuntimeError(f"{agent.name} failed: {stream_error_message(event)}")

            if anthropic:
                kind = event.get("type")
                if kind == "message_start":
                    if trace is not None:
                        usage = (event.get("message") or {}).get("usage")
                        if isinstance(usage, dict):
                            trace.usage.update(usage)
                elif kind == "content_block_delta":
                    delta = event.get("delta") or {}
                    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                        keep_text(delta["text"])
                    elif delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
                        keep_reasoning(delta["thinking"])
                elif kind == "message_delta":
                    stop_reason = (event.get("delta") or {}).get("stop_reason") or stop_reason
                    if trace is not None and isinstance(event.get("usage"), dict):
                        trace.usage.update(event["usage"])
                continue

            if trace is not None and isinstance(event.get("usage"), dict):
                trace.usage.update(event["usage"])
            choice = (event.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            if isinstance(delta.get("content"), str):
                keep_text(delta["content"])
            for key in ("reasoning_content", "reasoning"):
                if isinstance(delta.get(key), str):
                    keep_reasoning(delta[key])
                    break
            stop_reason = choice.get("finish_reason") or stop_reason
    finally:
        response.close()

    if trace is not None:
        trace.stop_reason = stop_reason or ""

    if stop_reason is None:
        raise AgentRuntimeError(f"{agent.name} failed: the stream ended before the answer was finished.")
    truncated = stop_reason == "max_tokens" if anthropic else stop_reason == "length"
    return "".join(parts), truncated


def adjusted_payload(agent: AIAgent, payload: dict, error: str) -> dict | None:
    """The next request for a provider 400 that only objects to a request field, else None."""
    anthropic = agent.provider == AgentProvider.ANTHROPIC
    if not anthropic and "stream_options" in error and "stream_options" in payload:
        return {key: value for key, value in payload.items() if key != "stream_options"}
    if "max_tokens" not in error or "max_tokens" not in payload:
        return None
    cap = output_cap_from_error(error, payload["max_tokens"])
    if cap is not None:
        return {**payload, "max_tokens": cap}
    if not anthropic:
        return {key: value for key, value in payload.items() if key != "max_tokens"}
    if payload["max_tokens"] > FALLBACK_MAX_OUTPUT_TOKENS:
        return {**payload, "max_tokens": FALLBACK_MAX_OUTPUT_TOKENS}
    return None


def invoke_agent(
    agent: AIAgent,
    messages: list,
    max_tokens: int | None = None,
    effort_override: str | None = None,
    model: str | None = None,
    allow_empty: bool = False,
    trace: AgentCallTrace | None = None,
    on_progress=None,
) -> str:
    """Send a chat conversation to the agent's provider and return the text answer."""
    api_key = decrypt_data(agent.api_key)
    if not api_key:
        raise AgentRuntimeError(f"No API key configured for {agent.name}.")

    anthropic = agent.provider == AgentProvider.ANTHROPIC
    if max_tokens is None:
        max_tokens = ANTHROPIC_MAX_OUTPUT_TOKENS if anthropic else OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS
    effort = effort_override if effort_override is not None else agent.reasoning_effort
    timeout = (STREAM_CONNECT_TIMEOUT, REQUEST_TIMEOUT)

    try:
        payload = provider_payload(agent, messages, max_tokens, effort, model, stream=True)
        # A provider 400/422 that only objects to a request field is resent without it
        for attempt in range(ADJUSTED_RESENDS + 1):
            if trace is not None:
                # Traced before the send, so a failure still leaves the prompt behind.
                # Only the request body: headers carry the API key and must never be traced
                trace.request = copy.deepcopy(payload)
                trace.max_tokens = payload.get("max_tokens")
            response = provider_post(agent, api_key, payload, timeout=timeout, stream=True)
            if response.status_code not in PARAMETER_ERROR_STATUSES or attempt == ADJUSTED_RESENDS:
                break
            adjusted = adjusted_payload(agent, payload, provider_error(response))
            if adjusted is None:
                break
            response.close()
            payload = adjusted
    except AgentRuntimeError:
        raise
    except Exception as e:
        log_exception(e)
        raise AgentRuntimeError(f"{agent.name} failed: {e}") from e

    if response.status_code != 200:
        error = provider_error(response)
        response.close()
        raise AgentRuntimeError(f"{agent.name} failed: {error}")

    if "text/event-stream" in str((response.headers or {}).get("Content-Type", "")):
        try:
            text, truncated = read_stream(agent, response, trace=trace, on_progress=on_progress)
        except AgentRuntimeError:
            raise
        except requests.RequestException as e:
            raise AgentRuntimeError(f"{agent.name} failed: {e}") from e
    else:
        try:
            data = response.json()
            if anthropic:
                blocks = data.get("content", [])
                text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text").strip()
                truncated = data.get("stop_reason") == "max_tokens"
                if trace is not None:
                    trace.text_parts.extend(block.get("text", "") for block in blocks if block.get("type") == "text")
                    trace.reasoning_parts.extend(
                        block.get("thinking", "") for block in blocks if block.get("type") == "thinking"
                    )
                    trace.stop_reason = data.get("stop_reason") or ""
                    if isinstance(data.get("usage"), dict):
                        trace.usage.update(data["usage"])
            else:
                choice = (data.get("choices") or [{}])[0]
                message = choice.get("message") or {}
                text = (message.get("content") or "").strip()
                truncated = choice.get("finish_reason") == "length"
                if trace is not None:
                    if isinstance(message.get("content"), str):
                        trace.text_parts.append(message["content"])
                    for key in ("reasoning_content", "reasoning"):
                        if isinstance(message.get(key), str):
                            trace.reasoning_parts.append(message[key])
                            break
                    trace.stop_reason = choice.get("finish_reason") or ""
                    if isinstance(data.get("usage"), dict):
                        trace.usage.update(data["usage"])
        finally:
            response.close()

    text = text.strip()
    if not text:
        if allow_empty:
            return ""
        raise AgentRuntimeError(f"{agent.name} returned no text.")

    if truncated:
        logger.warning("Agent %s output was truncated at the token limit.", agent.id)
        text = f"{text}{TRUNCATION_NOTICE}"
    return text


def check_agent_connection(agent: AIAgent) -> tuple[bool, str]:
    """Send one minimal request to the provider so bad credentials surface in the UI, not later."""
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
