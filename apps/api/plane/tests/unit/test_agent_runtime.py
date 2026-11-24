# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock, patch

from plane.db.models import OPENCODE_BASE_URL, AgentProvider, AIAgent
from plane.license.utils.encryption import encrypt_data
from plane.utils.agent_runtime import (
    check_agent_connection,
    extract_json,
    markdown_to_html,
    provider_headers,
    provider_payload,
    provider_url,
    retry_after_seconds,
)


def _agent(provider=AgentProvider.OPENCODE, api_key="sk-test", base_url="", reasoning_effort=""):
    return AIAgent(
        id="6f1d5a52-0000-4000-8000-000000000000",
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


def test_extract_json_plain_object():
    assert extract_json('{"passed": true}') == {"passed": True}


def test_extract_json_strips_code_fences_and_prose():
    text = 'Here is the result:\n```json\n{"tasks": [{"title": "One"}]}\n```\nDone.'
    assert extract_json(text) == {"tasks": [{"title": "One"}]}


def test_extract_json_returns_none_when_no_object():
    assert extract_json("no json here") is None
    assert extract_json("{broken") is None


def test_provider_url_defaults_to_opencode_and_appends_chat_completions():
    assert provider_url(_agent()) == f"{OPENCODE_BASE_URL}/chat/completions"
    assert provider_url(_agent(base_url="https://opencode.ai/inference/go/team")) == (
        "https://opencode.ai/inference/go/team/chat/completions"
    )


def test_provider_headers_send_a_bearer_token():
    headers = provider_headers(_agent(), "sk-secret")

    assert headers["Authorization"] == "Bearer sk-secret"
    assert headers["content-type"] == "application/json"


def test_opencode_go_headers_send_a_session_id():
    agent = _agent(base_url="https://opencode.ai/inference/go/team")

    headers = provider_headers(agent, "sk-secret")

    assert headers["x-opencode-session"] == str(agent.id)
    assert headers["user-agent"] == "plane-ai-agent/1.0"


def test_provider_payload_is_not_streaming():
    payload = provider_payload(_agent(), [{"role": "user", "content": "hi"}], 16, "high", "override")

    assert payload["model"] == "override"
    assert payload["messages"] == [{"role": "user", "content": "hi"}]
    assert payload["max_tokens"] == 16
    assert payload["reasoning_effort"] == "high"
    assert "stream" not in payload
    assert "stream_options" not in payload


def test_provider_payload_adds_the_system_prompt_as_a_system_message():
    agent = _agent()
    agent.system_prompt = "Be brief."

    payload = provider_payload(agent, [{"role": "user", "content": "hi"}], 16)

    assert payload["messages"][0] == {"role": "system", "content": "Be brief."}


def test_retry_after_seconds_reads_the_header():
    assert retry_after_seconds(MagicMock(headers={"Retry-After": "5"})) == 5
    assert retry_after_seconds(MagicMock(headers={"Retry-After": "soon"})) is None
    assert retry_after_seconds(MagicMock(headers={})) is None


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_transient_statuses_are_retried_with_backoff(mock_fetch, mock_sleep):
    mock_fetch.side_effect = [_response(500), _response(200)]

    ok, message = check_agent_connection(_agent())

    assert (ok, message) == (True, "Connection successful.")
    assert mock_fetch.call_count == 2
    assert [call.args[0] for call in mock_sleep.call_args_list] == [1]


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_retry_after_is_honoured_and_clamped(mock_fetch, mock_sleep):
    mock_fetch.side_effect = [
        _response(429, headers={"Retry-After": "5"}),
        _response(429, headers={"Retry-After": "120"}),
        _response(200),
    ]

    assert check_agent_connection(_agent())[0] is True
    assert [call.args[0] for call in mock_sleep.call_args_list] == [5, 30]


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_a_client_error_is_not_retried(mock_fetch):
    mock_fetch.return_value = _response(400, {"error": {"message": "bad request"}})

    ok, message = check_agent_connection(_agent())

    assert ok is False
    assert "HTTP 400" in message
    assert "bad request" in message
    assert mock_fetch.call_count == 1


def test_other_providers_are_refused():
    with patch("plane.utils.agent_runtime.pinned_fetch") as mock_fetch:
        ok, message = check_agent_connection(_agent(provider="anthropic"))

    assert (ok, message) == (False, "Only the OpenCode provider is supported.")
    mock_fetch.assert_not_called()


def test_missing_api_key_is_reported_without_calling_the_provider():
    with patch("plane.utils.agent_runtime.pinned_fetch") as mock_fetch:
        ok, message = check_agent_connection(_agent(api_key=""))

    assert (ok, message) == (False, "No API key configured for this agent.")
    mock_fetch.assert_not_called()


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_invalid_key_is_reported(mock_fetch):
    mock_fetch.return_value = _response(401)

    assert check_agent_connection(_agent()) == (False, "Authentication failed. Check the API key.")


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_missing_model_is_reported(mock_fetch):
    mock_fetch.return_value = _response(404)

    assert check_agent_connection(_agent()) == (False, "Model not found. Check the model name or base URL.")


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_provider_calls_go_through_the_ssrf_guard(mock_fetch):
    mock_fetch.return_value = _response(200)

    check_agent_connection(_agent(base_url="https://opencode.ai/inference/go/team"))

    assert mock_fetch.call_args.args[:2] == (
        "POST",
        "https://opencode.ai/inference/go/team/chat/completions",
    )
    assert mock_fetch.call_args.kwargs["allowed_ips"] is not None
    assert mock_fetch.call_args.kwargs["allowed_hosts"] is not None
    # The stored key is decrypted for the provider call, never sent as the raw stored value
    assert mock_fetch.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_effort_is_sent_and_omitted_when_not_set(mock_fetch):
    mock_fetch.return_value = _response(200)

    check_agent_connection(_agent(reasoning_effort="low"))
    assert mock_fetch.call_args.kwargs["json"]["reasoning_effort"] == "low"

    check_agent_connection(_agent())
    assert "reasoning_effort" not in mock_fetch.call_args.kwargs["json"]


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_connection_errors_are_retried_then_reported(mock_fetch, mock_sleep):
    import requests

    mock_fetch.side_effect = requests.ConnectionError("boom")

    ok, message = check_agent_connection(_agent())

    assert ok is False
    assert "Could not reach the provider" in message
    assert mock_fetch.call_count == 3


def test_markdown_to_html_escapes_everything_first():
    assert markdown_to_html("<script>alert(1)</script>") == "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"


def test_markdown_to_html_paragraphs_and_line_breaks():
    assert markdown_to_html("one\ntwo\n\nthree") == "<p>one<br>two</p><p>three</p>"


def test_markdown_to_html_code_fences_survive_blank_lines():
    text = "before\n\n```python\nx = 1\n\ny = 2\n```\n\nafter"

    assert markdown_to_html(text) == ("<p>before</p><pre><code>x = 1\n\ny = 2</code></pre><p>after</p>")


def test_markdown_to_html_keeps_text_longer_than_20000_characters():
    text = "a" * 50000

    assert markdown_to_html(text) == f"<p>{text}</p>"
