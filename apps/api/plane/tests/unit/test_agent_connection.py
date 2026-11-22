# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock, patch

import httpx
from openai import APIStatusError

from plane.app.views.agent import check_agent_connection
from plane.db.models import AgentProvider, AIAgent
from plane.license.utils.encryption import encrypt_data


def _agent(provider=AgentProvider.ANTHROPIC, api_key="sk-test", base_url="", reasoning_effort=""):
    return AIAgent(
        provider=provider,
        model="claude-test",
        api_key=encrypt_data(api_key) if api_key else "",
        base_url=base_url,
        reasoning_effort=reasoning_effort,
    )


def test_missing_api_key_is_reported_without_calling_the_provider():
    assert check_agent_connection(_agent(api_key="")) == (False, "No API key configured for this agent.")


@patch("plane.app.views.agent.requests.post")
def test_anthropic_rejects_invalid_key(mock_post):
    mock_post.return_value = MagicMock(status_code=401)

    ok, message = check_agent_connection(_agent())

    assert ok is False
    assert message == "Authentication failed. Check the API key."
    # The stored key is decrypted for the provider call, never sent as the raw stored value
    assert mock_post.call_args.kwargs["headers"]["x-api-key"] == "sk-test"


@patch("plane.app.views.agent.requests.post")
def test_anthropic_surfaces_the_provider_error_message(mock_post):
    response = MagicMock(status_code=400)
    response.json.return_value = {"error": {"message": "Your credit balance is too low."}}
    mock_post.return_value = response

    ok, message = check_agent_connection(_agent())

    assert ok is False
    assert "HTTP 400" in message
    assert "credit balance is too low" in message


@patch("plane.app.views.agent.requests.post")
def test_anthropic_success(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    assert check_agent_connection(_agent()) == (True, "Connection successful.")


@patch("plane.app.views.agent.OpenAI")
def test_openai_compatible_success(mock_openai):
    assert check_agent_connection(_agent(provider=AgentProvider.OPENCODE)) == (True, "Connection successful.")
    mock_openai.assert_called_once()


@patch("plane.app.views.agent.requests.post")
def test_anthropic_sends_the_effort_level(mock_post):
    mock_post.return_value = MagicMock(status_code=200)

    check_agent_connection(_agent(reasoning_effort="xhigh"))

    assert mock_post.call_args.kwargs["json"]["output_config"] == {"effort": "xhigh"}


@patch("plane.app.views.agent.OpenAI")
def test_openai_compatible_sends_the_effort_level(mock_openai):
    agent = _agent(provider=AgentProvider.OPENCODE, reasoning_effort="low")

    check_agent_connection(agent)

    assert mock_openai.return_value.chat.completions.create.call_args.kwargs["reasoning_effort"] == "low"


@patch("plane.app.views.agent.OpenAI")
def test_effort_is_omitted_when_not_set(mock_openai):
    check_agent_connection(_agent(provider=AgentProvider.OPENCODE))

    assert "reasoning_effort" not in mock_openai.return_value.chat.completions.create.call_args.kwargs


@patch("plane.app.views.agent.OpenAI")
def test_opencode_go_sends_a_session_header(mock_openai):
    agent = _agent(provider=AgentProvider.OPENCODE, base_url="https://opencode.ai/inference/go/v1")

    check_agent_connection(agent)

    headers = mock_openai.call_args.kwargs["default_headers"]
    assert headers["x-opencode-session"] == str(agent.id)


@patch("plane.app.views.agent.OpenAI")
def test_openai_compatible_status_error_reports_the_http_code_and_detail(mock_openai):
    response = httpx.Response(405, request=httpx.Request("POST", "https://example.test/v1/chat/completions"))
    mock_openai.return_value.chat.completions.create.side_effect = APIStatusError(
        "Error code: 405",
        response=response,
        body={"error": {"message": "Request is missing x-opencode-session."}},
    )

    ok, message = check_agent_connection(_agent(provider=AgentProvider.OPENCODE))

    assert ok is False
    assert "405" in message
    assert "missing x-opencode-session" in message
