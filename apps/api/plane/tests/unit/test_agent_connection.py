# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock, patch

from plane.app.views.agent import check_agent_connection
from plane.db.models import AgentProvider, AIAgent
from plane.license.utils.encryption import encrypt_data


def _agent(provider=AgentProvider.OPENCODE, api_key="sk-test", base_url="", reasoning_effort=""):
    return AIAgent(
        provider=provider,
        model="opencode-test",
        api_key=encrypt_data(api_key) if api_key else "",
        base_url=base_url,
        reasoning_effort=reasoning_effort,
    )


def _response(status_code=200, payload=None, headers=None):
    response = MagicMock(status_code=status_code, headers=headers or {})
    response.json.return_value = payload or {}
    return response


def test_the_endpoint_uses_the_shared_check():
    assert check_agent_connection(_agent(api_key="")) == (False, "No API key configured for this agent.")


def test_non_opencode_agents_are_refused():
    assert check_agent_connection(_agent(provider="anthropic")) == (
        False,
        "Only the OpenCode provider is supported.",
    )


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_opencode_success(mock_fetch):
    mock_fetch.return_value = _response(200)

    assert check_agent_connection(_agent()) == (True, "Connection successful.")
    assert mock_fetch.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_a_status_error_reports_the_http_code_and_detail(mock_fetch):
    mock_fetch.return_value = _response(405, {"error": {"message": "Request is missing x-opencode-session."}})

    ok, message = check_agent_connection(_agent())

    assert ok is False
    assert "405" in message
    assert "missing x-opencode-session" in message
