# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock, patch

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


def _response(status_code=200, payload=None, headers=None):
    response = MagicMock(status_code=status_code, headers=headers or {})
    response.json.return_value = payload or {}
    return response


def test_missing_api_key_is_reported_without_calling_the_provider():
    assert check_agent_connection(_agent(api_key="")) == (False, "No API key configured for this agent.")


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_rejects_invalid_key(mock_fetch):
    mock_fetch.return_value = _response(401)

    ok, message = check_agent_connection(_agent())

    assert ok is False
    assert message == "Authentication failed. Check the API key."
    # The stored key is decrypted for the provider call, never sent as the raw stored value
    assert mock_fetch.call_args.kwargs["headers"]["x-api-key"] == "sk-test"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_provider_calls_go_through_the_ssrf_guard(mock_fetch):
    mock_fetch.return_value = _response(200)

    check_agent_connection(_agent(base_url="https://user-provided.example.com"))

    assert mock_fetch.call_args.args[:2] == ("POST", "https://user-provided.example.com/v1/messages")
    assert mock_fetch.call_args.kwargs["allowed_ips"] is not None
    assert mock_fetch.call_args.kwargs["allowed_hosts"] is not None


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_surfaces_the_provider_error_message(mock_fetch):
    mock_fetch.return_value = _response(400, {"error": {"message": "Your credit balance is too low."}})

    ok, message = check_agent_connection(_agent())

    assert ok is False
    assert "HTTP 400" in message
    assert "credit balance is too low" in message


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_success(mock_fetch):
    mock_fetch.return_value = _response(200)

    assert check_agent_connection(_agent()) == (True, "Connection successful.")


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_compatible_success_uses_a_direct_post(mock_fetch):
    mock_fetch.return_value = _response(200)

    assert check_agent_connection(_agent(provider=AgentProvider.OPENCODE)) == (True, "Connection successful.")
    assert mock_fetch.call_args.args[1] == "https://api.openai.com/v1/chat/completions"
    assert mock_fetch.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_sends_the_effort_level(mock_fetch):
    mock_fetch.return_value = _response(200)

    check_agent_connection(_agent(reasoning_effort="xhigh"))

    assert mock_fetch.call_args.kwargs["json"]["output_config"] == {"effort": "xhigh"}


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_compatible_sends_the_effort_level(mock_fetch):
    mock_fetch.return_value = _response(200)

    check_agent_connection(_agent(provider=AgentProvider.OPENCODE, reasoning_effort="low"))

    assert mock_fetch.call_args.kwargs["json"]["reasoning_effort"] == "low"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_effort_is_omitted_when_not_set(mock_fetch):
    mock_fetch.return_value = _response(200)

    check_agent_connection(_agent(provider=AgentProvider.OPENCODE))

    assert "reasoning_effort" not in mock_fetch.call_args.kwargs["json"]


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_opencode_go_sends_a_session_header(mock_fetch):
    mock_fetch.return_value = _response(200)
    agent = _agent(provider=AgentProvider.OPENCODE, base_url="https://opencode.ai/inference/go/v1")

    check_agent_connection(agent)

    headers = mock_fetch.call_args.kwargs["headers"]
    assert headers["x-opencode-session"] == str(agent.id)


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_compatible_status_error_reports_the_http_code_and_detail(mock_fetch):
    mock_fetch.return_value = _response(405, {"error": {"message": "Request is missing x-opencode-session."}})

    ok, message = check_agent_connection(_agent(provider=AgentProvider.OPENCODE))

    assert ok is False
    assert "405" in message
    assert "missing x-opencode-session" in message
