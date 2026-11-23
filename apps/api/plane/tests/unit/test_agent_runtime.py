# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock, patch

import pytest
import requests

from plane.db.models import AgentProvider, AIAgent
from plane.license.utils.encryption import encrypt_data
from plane.utils.agent_runtime import (
    TRUNCATION_NOTICE,
    AgentRuntimeError,
    extract_json,
    invoke_agent,
    markdown_to_html,
)


def _agent(provider=AgentProvider.ANTHROPIC):
    return AIAgent(
        provider=provider,
        model="claude-test",
        api_key=encrypt_data("sk-test"),
        base_url="",
        reasoning_effort="",
    )


def _response(status_code=200, payload=None, headers=None):
    response = MagicMock(status_code=status_code, headers=headers or {})
    response.json.return_value = payload or {}
    return response


def _anthropic_text(text, stop_reason="end_turn"):
    return _response(200, {"content": [{"type": "text", "text": text}], "stop_reason": stop_reason})


def _openai_text(text, finish_reason="stop"):
    return _response(200, {"choices": [{"message": {"content": text}, "finish_reason": finish_reason}]})


def test_extract_json_plain_object():
    assert extract_json('{"passed": true}') == {"passed": True}


def test_extract_json_strips_code_fences_and_prose():
    text = 'Here is the result:\n```json\n{"tasks": [{"title": "One"}]}\n```\nDone.'
    assert extract_json(text) == {"tasks": [{"title": "One"}]}


def test_extract_json_returns_none_when_no_object():
    assert extract_json("no json here") is None
    assert extract_json("{broken") is None


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_retries_a_transient_status_then_succeeds(mock_fetch, mock_sleep):
    mock_fetch.side_effect = [_response(500), _anthropic_text("done")]

    assert invoke_agent(_agent(), [{"role": "user", "content": "hi"}]) == "done"
    assert mock_fetch.call_count == 2
    assert [call.args[0] for call in mock_sleep.call_args_list] == [1]


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_retries_a_connection_error_with_backoff(mock_fetch, mock_sleep):
    mock_fetch.side_effect = [requests.ConnectionError("boom"), _anthropic_text("done")]

    assert invoke_agent(_agent(), [{"role": "user", "content": "hi"}]) == "done"
    assert [call.args[0] for call in mock_sleep.call_args_list] == [1]


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_gives_up_after_three_attempts(mock_fetch, mock_sleep):
    mock_fetch.return_value = _response(503)

    with pytest.raises(AgentRuntimeError):
        invoke_agent(_agent(), [{"role": "user", "content": "hi"}])

    assert mock_fetch.call_count == 3
    assert [call.args[0] for call in mock_sleep.call_args_list] == [1, 3]


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_connection_errors_are_retried_then_reported(mock_fetch, mock_sleep):
    mock_fetch.side_effect = requests.ConnectionError("boom")

    with pytest.raises(AgentRuntimeError):
        invoke_agent(_agent(), [{"role": "user", "content": "hi"}])

    assert mock_fetch.call_count == 3


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_retry_after_is_honoured_and_clamped(mock_fetch, mock_sleep):
    mock_fetch.side_effect = [
        _response(429, headers={"Retry-After": "5"}),
        _response(429, headers={"Retry-After": "120"}),
        _anthropic_text("done"),
    ]

    assert invoke_agent(_agent(), [{"role": "user", "content": "hi"}]) == "done"
    assert [call.args[0] for call in mock_sleep.call_args_list] == [5, 30]


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_a_client_error_is_not_retried(mock_fetch, mock_sleep):
    mock_fetch.return_value = _response(400, {"error": {"message": "bad request"}})

    with pytest.raises(AgentRuntimeError):
        invoke_agent(_agent(), [{"role": "user", "content": "hi"}])

    assert mock_fetch.call_count == 1
    mock_sleep.assert_not_called()


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_truncation_is_flagged(mock_fetch):
    mock_fetch.return_value = _anthropic_text("partial", stop_reason="max_tokens")

    text = invoke_agent(_agent(), [{"role": "user", "content": "hi"}])

    assert text == f"partial{TRUNCATION_NOTICE}"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_truncation_is_flagged(mock_fetch):
    mock_fetch.return_value = _openai_text("partial", finish_reason="length")

    text = invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}])

    assert text.endswith(TRUNCATION_NOTICE)
    assert mock_fetch.call_args.args[1] == "https://api.openai.com/v1/chat/completions"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_model_override_replaces_the_agent_model(mock_fetch):
    mock_fetch.return_value = _anthropic_text("ok")

    invoke_agent(_agent(), [{"role": "user", "content": "hi"}], model="claude-haiku")

    assert mock_fetch.call_args.kwargs["json"]["model"] == "claude-haiku"


def test_markdown_to_html_escapes_everything_first():
    assert markdown_to_html("<script>alert(1)</script>") == "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>"


def test_markdown_to_html_paragraphs_and_line_breaks():
    assert markdown_to_html("one\ntwo\n\nthree") == "<p>one<br>two</p><p>three</p>"


def test_markdown_to_html_code_fences_survive_blank_lines():
    text = "before\n\n```python\nx = 1\n\ny = 2\n```\n\nafter"

    assert markdown_to_html(text) == ("<p>before</p><pre><code>x = 1\n\ny = 2</code></pre><p>after</p>")


def test_markdown_to_html_truncates():
    html = markdown_to_html("a" * 50000)

    assert len(html) < 25000
    assert html.startswith("<p>")
    assert html.endswith("</p>")
