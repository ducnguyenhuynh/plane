# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from plane.db.models import AgentProvider, AIAgent
from plane.license.utils.encryption import encrypt_data
from plane.utils.agent_runtime import (
    ANTHROPIC_MAX_OUTPUT_TOKENS,
    OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS,
    TRUNCATION_NOTICE,
    AgentCallTrace,
    AgentRuntimeError,
    extract_json,
    invoke_agent,
    markdown_to_html,
    output_cap_from_error,
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


def _sse(payload):
    return b"data: " + json.dumps(payload).encode()


def _stream(chunks):
    response = _response(200, headers={"Content-Type": "text/event-stream"})
    response.iter_lines.return_value = chunks
    return response


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


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_request_streams_with_the_anthropic_default_output_budget(mock_fetch):
    mock_fetch.return_value = _anthropic_text("ok")

    invoke_agent(_agent(), [{"role": "user", "content": "hi"}])

    payload = mock_fetch.call_args.kwargs["json"]
    assert payload["stream"] is True
    assert payload["max_tokens"] == 128000
    assert payload["max_tokens"] == ANTHROPIC_MAX_OUTPUT_TOKENS
    assert mock_fetch.call_args.kwargs["stream"] is True


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_request_streams_with_the_openai_default_output_budget(mock_fetch):
    mock_fetch.return_value = _openai_text("ok")

    invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}])

    payload = mock_fetch.call_args.kwargs["json"]
    assert payload["max_tokens"] == 384000
    assert payload["max_tokens"] == OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS
    # Usage only arrives on the stream when it is asked for
    assert payload["stream_options"] == {"include_usage": True}


def test_output_cap_from_error_reads_the_reported_cap():
    assert output_cap_from_error("max_tokens: 128000 > 64000, which is the maximum", 128000) == 64000
    assert output_cap_from_error("valid range of max_tokens is [1, 8192]", 384000) == 8192


def test_output_cap_from_error_returns_none_without_a_smaller_cap():
    assert output_cap_from_error("max_tokens is not supported", 64000) is None
    assert output_cap_from_error("max_tokens: 64000 > 128000", 64000) is None


def test_output_cap_from_error_ignores_digits_inside_model_names():
    assert output_cap_from_error("max_tokens too large for qwen4-max", 128000) is None
    assert output_cap_from_error("max_tokens invalid for claude-haiku-5-5", 128000) is None
    assert output_cap_from_error("max_tokens invalid for deepseek-v4.1-flash", 128000) is None


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_max_tokens_400_resends_with_the_reported_cap(mock_fetch):
    mock_fetch.side_effect = [
        _response(400, {"error": {"message": "max_tokens: 128000 > 64000, which is the maximum"}}),
        _anthropic_text("ok"),
    ]

    assert invoke_agent(_agent(), [{"role": "user", "content": "hi"}]) == "ok"
    assert mock_fetch.call_count == 2
    assert mock_fetch.call_args.kwargs["json"]["max_tokens"] == 64000


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_max_tokens_400_resends_with_the_reported_cap(mock_fetch):
    mock_fetch.side_effect = [
        _response(400, {"error": {"message": "valid range of max_tokens is [1, 8192]"}}),
        _openai_text("ok"),
    ]

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == "ok"
    assert mock_fetch.call_args.kwargs["json"]["max_tokens"] == 8192


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_400_naming_max_tokens_without_a_cap_drops_max_tokens(mock_fetch):
    mock_fetch.side_effect = [
        _response(400, {"error": {"message": "max_tokens too large for qwen4-max"}}),
        _openai_text("ok"),
    ]

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == "ok"
    assert mock_fetch.call_count == 2
    # The "4" in qwen4-max is not an output budget
    assert "max_tokens" not in mock_fetch.call_args.kwargs["json"]


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_422_naming_max_tokens_is_adjusted_and_resent(mock_fetch):
    mock_fetch.side_effect = [
        _response(422, {"error": {"message": "valid range of max_tokens is [1, 8192]"}}),
        _openai_text("ok"),
    ]

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == "ok"
    assert mock_fetch.call_count == 2
    assert mock_fetch.call_args.kwargs["json"]["max_tokens"] == 8192


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_stream_options_400_resends_without_stream_options(mock_fetch):
    mock_fetch.side_effect = [
        _response(400, {"error": {"message": "Unsupported parameter: stream_options"}}),
        _openai_text("ok"),
    ]

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == "ok"
    payload = mock_fetch.call_args.kwargs["json"]
    assert "stream_options" not in payload
    assert "max_tokens" in payload


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_stream_returns_joined_text(mock_fetch):
    mock_fetch.return_value = _stream(
        [
            b"event: message_start",
            _sse({"type": "message_start", "message": {}}),
            b"",
            _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}),
            _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}}),
            _sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
        ]
    )

    assert invoke_agent(_agent(), [{"role": "user", "content": "hi"}]) == "Hello world"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_stream_returns_joined_text_and_ignores_reasoning(mock_fetch):
    mock_fetch.return_value = _stream(
        [
            _sse({"choices": [{"delta": {"reasoning_content": "thinking hard"}}]}),
            _sse({"choices": [{"delta": {"content": "Done"}}]}),
            _sse({"choices": []}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]",
        ]
    )

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == "Done"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_stream_with_a_null_error_chunk_returns_text(mock_fetch):
    mock_fetch.return_value = _stream(
        [
            _sse({"error": None, "choices": [{"delta": {"content": "Done"}}]}),
            _sse({"error": None, "choices": [{"delta": {}, "finish_reason": "stop"}]}),
            b"data: [DONE]",
        ]
    )

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == "Done"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_a_json_response_is_closed(mock_fetch):
    response = _openai_text("ok")
    mock_fetch.return_value = response

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == "ok"
    response.close.assert_called_once()


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_stream_truncation_is_flagged(mock_fetch):
    mock_fetch.return_value = _stream(
        [
            _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "partial"}}),
            _sse({"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}),
        ]
    )

    assert invoke_agent(_agent(), [{"role": "user", "content": "hi"}]) == f"partial{TRUNCATION_NOTICE}"


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_stream_truncation_is_flagged(mock_fetch):
    mock_fetch.return_value = _stream(
        [
            _sse({"choices": [{"delta": {"content": "partial"}}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "length"}]}),
            b"data: [DONE]",
        ]
    )

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == (
        f"partial{TRUNCATION_NOTICE}"
    )


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_truncated_stream_without_text_raises_without_the_notice(mock_fetch):
    mock_fetch.return_value = _stream([_sse({"type": "message_delta", "delta": {"stop_reason": "max_tokens"}})])

    with pytest.raises(AgentRuntimeError) as error:
        invoke_agent(_agent(), [{"role": "user", "content": "hi"}])

    assert "no text" in str(error.value)
    assert TRUNCATION_NOTICE not in str(error.value)


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_truncated_stream_without_text_returns_empty_when_allowed(mock_fetch):
    mock_fetch.return_value = _stream([_sse({"type": "message_delta", "delta": {"stop_reason": "max_tokens"}})])

    assert invoke_agent(_agent(), [{"role": "user", "content": "hi"}], allow_empty=True) == ""


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_stream_without_a_finish_reason_raises(mock_fetch):
    mock_fetch.return_value = _stream([_sse({"choices": [{"delta": {"content": "cut off"}}]})])

    with pytest.raises(AgentRuntimeError):
        invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}])


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_max_tokens_rejection_is_resent_without_max_tokens(mock_fetch):
    mock_fetch.side_effect = [
        _response(400, {"error": {"message": "Unsupported parameter: max_tokens"}}),
        _stream([_sse({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}), b"data: [DONE]"]),
    ]

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}]) == "ok"
    assert mock_fetch.call_count == 2
    assert "max_tokens" not in mock_fetch.call_args.kwargs["json"]


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_anthropic_stream_fills_the_trace(mock_fetch):
    mock_fetch.return_value = _stream(
        [
            _sse({"type": "message_start", "message": {"usage": {"input_tokens": 11}}}),
            _sse({"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": "pondering"}}),
            _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hello"}}),
            _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": " world"}}),
            _sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 7}}),
        ]
    )
    trace = AgentCallTrace()

    assert invoke_agent(_agent(), [{"role": "user", "content": "hi"}], trace=trace) == "Hello world"
    assert trace.text == "Hello world"
    assert trace.reasoning == "pondering"
    assert trace.usage == {"input_tokens": 11, "output_tokens": 7}
    assert trace.stop_reason == "end_turn"
    assert trace.max_tokens == ANTHROPIC_MAX_OUTPUT_TOKENS
    assert trace.request["messages"] == [{"role": "user", "content": "hi"}]
    # Only the body is traced: headers carry the decrypted key
    assert "sk-test" not in json.dumps(trace.request)


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_openai_stream_fills_the_trace_including_usage_on_an_empty_final_chunk(mock_fetch):
    mock_fetch.return_value = _stream(
        [
            _sse({"choices": [{"delta": {"reasoning_content": "thinking"}}]}),
            _sse({"choices": [{"delta": {"content": "Done"}}]}),
            _sse({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
            _sse({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}}),
            b"data: [DONE]",
        ]
    )
    trace = AgentCallTrace()

    assert invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}], trace=trace) == "Done"
    assert trace.text == "Done"
    assert trace.reasoning == "thinking"
    assert trace.usage == {"prompt_tokens": 5, "completion_tokens": 2}
    assert trace.stop_reason == "stop"
    assert "sk-test" not in json.dumps(trace.request)


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_partial_text_stays_in_the_trace_when_the_stream_ends_early(mock_fetch):
    mock_fetch.return_value = _stream([_sse({"choices": [{"delta": {"content": "half"}}]})])
    trace = AgentCallTrace()

    with pytest.raises(AgentRuntimeError):
        invoke_agent(_agent(AgentProvider.OPENCODE), [{"role": "user", "content": "hi"}], trace=trace)

    assert trace.text == "half"


@patch("plane.utils.agent_runtime.time.sleep")
@patch("plane.utils.agent_runtime.pinned_fetch")
def test_a_connection_failure_leaves_the_request_in_the_trace(mock_fetch, mock_sleep):
    mock_fetch.side_effect = requests.ConnectionError("boom")
    trace = AgentCallTrace()

    with pytest.raises(AgentRuntimeError):
        invoke_agent(_agent(), [{"role": "user", "content": "hi"}], trace=trace)

    assert trace.request["messages"] == [{"role": "user", "content": "hi"}]
    assert trace.max_tokens == ANTHROPIC_MAX_OUTPUT_TOKENS


@patch("plane.utils.agent_runtime.pinned_fetch")
def test_a_raising_on_progress_stops_the_stream_and_closes_the_response(mock_fetch):
    response = _stream(
        [
            _sse({"type": "content_block_delta", "delta": {"type": "text_delta", "text": "one"}}),
            _sse({"type": "message_delta", "delta": {"stop_reason": "end_turn"}}),
        ]
    )
    mock_fetch.return_value = response

    def stop():
        raise AgentRuntimeError("The agent run is no longer active.")

    with pytest.raises(AgentRuntimeError):
        invoke_agent(_agent(), [{"role": "user", "content": "hi"}], on_progress=stop)

    response.close.assert_called_once()


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
