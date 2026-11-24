# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

from plane.license.utils.encryption import encrypt_data
from plane.utils.agent_runner_client import (
    AgentRunnerError,
    SandboxCall,
    SandboxTrace,
    run_sandbox,
)


def _call(agent=None):
    return SandboxCall(
        agent=agent
        or SimpleNamespace(
            id="agent",
            name="Agent",
            api_key=encrypt_data("sk-test"),
            base_url="",
        ),
        model="opencode-test",
        effort="",
        prompt="Do the work.",
        title="ENG-1 develop",
        session_id="ses_plane_x",
        sandbox_key="abc-develop-123",
        workspace="runs/run/tasks/task",
        sessions="sessions/issue",
        read_only=False,
    )


def _response(events=(), status_code=200, payload=None):
    response = MagicMock(status_code=status_code)
    response.iter_lines.return_value = [json.dumps(event).encode() for event in events]
    response.json.return_value = payload or {}
    return response


def _event(payload):
    return {"type": "event", "event": payload}


def _step_start():
    return _event({"type": "step_start"})


def _text(part_id, text):
    return _event({"type": "text", "part": {"id": part_id, "text": text}})


def _reasoning(part_id, text):
    return _event({"type": "reasoning", "part": {"id": part_id, "text": text}})


def _tool(part_id, tool, status="completed", title="run", output="ok"):
    return _event(
        {
            "type": "tool_use",
            "part": {
                "id": part_id,
                "tool": tool,
                "state": {"status": status, "title": title, "input": {"cmd": "ls"}, "output": output},
            },
        }
    )


def _finish(reason="stop", cost=None, input_tokens=0, output_tokens=0, cache=None):
    part = {"reason": reason}
    if cost is not None:
        part["cost"] = cost
    part["tokens"] = {"input": input_tokens, "output": output_tokens, "reasoning": 0, "cache": cache or {}}
    return _event({"type": "step_finish", "part": part})


def _exit(code=0, timed_out=False):
    return {"type": "exit", "code": code, "timed_out": timed_out}


def _log(stream, line):
    return {"type": "log", "stream": stream, "line": line}


@pytest.fixture(autouse=True)
def runner_env(monkeypatch):
    monkeypatch.setenv("AGENT_RUNNER_TOKEN", "runner-token")
    monkeypatch.setenv("AGENT_RUNNER_URL", "http://runner:9999")


@patch("plane.utils.agent_runner_client.requests.post")
def test_text_comes_from_the_last_step_that_has_text(post):
    post.return_value = _response(
        [
            _step_start(),
            _text("p1", "first"),
            _finish(),
            _step_start(),
            _text("p2", "second "),
            _text("p3", "answer"),
            _finish(),
            _step_start(),
            _finish(),
            _exit(),
        ]
    )
    trace = SandboxTrace()

    assert run_sandbox(_call(), trace) == "second answer"
    assert trace.text == "second answer"
    assert trace.stop_reason == "stop"


@patch("plane.utils.agent_runner_client.requests.post")
def test_text_parts_are_upserted_by_id(post):
    post.return_value = _response(
        [
            _step_start(),
            _text("p1", "Hel"),
            _text("p1", "Hello"),
            _finish(),
            _exit(),
        ]
    )
    trace = SandboxTrace()

    assert run_sandbox(_call(), trace) == "Hello"


@patch("plane.utils.agent_runner_client.requests.post")
def test_reasoning_parts_join_with_a_blank_line_and_upsert(post):
    post.return_value = _response(
        [
            _reasoning("r1", "thinking"),
            _reasoning("r2", "more"),
            _reasoning("r1", "thinking harder"),
            _exit(),
        ]
    )
    trace = SandboxTrace()

    run_sandbox(_call(), trace)

    assert trace.reasoning == "thinking harder\n\nmore"


@patch("plane.utils.agent_runner_client.requests.post")
def test_tool_calls_are_collected_once_per_part(post):
    post.return_value = _response(
        [
            _tool("t1", "bash", status="running", title="running ls", output=""),
            _tool("t1", "bash", title="listed", output="app.py"),
            _tool("t2", "read"),
            _exit(),
        ]
    )
    trace = SandboxTrace()

    run_sandbox(_call(), trace)

    assert [call["tool"] for call in trace.tool_calls] == ["bash", "read"]
    assert trace.tool_calls[0]["status"] == "completed"
    assert trace.tool_calls[0]["output"] == "app.py"
    assert trace.tool_calls[0]["input"] == {"cmd": "ls"}


@patch("plane.utils.agent_runner_client.requests.post")
def test_usage_and_cost_are_summed_across_steps(post):
    post.return_value = _response(
        [
            _finish(cost="0.10", input_tokens=10, output_tokens=5, cache={"read": 2, "write": 1}),
            _finish(cost="0.05", input_tokens=3, output_tokens=4),
            _exit(),
        ]
    )
    trace = SandboxTrace()

    run_sandbox(_call(), trace)

    assert trace.cost == Decimal("0.15")
    assert trace.usage == {
        "input_tokens": 13,
        "output_tokens": 9,
        "reasoning_tokens": 0,
        "cache_read_tokens": 2,
        "cache_write_tokens": 1,
    }


@patch("plane.utils.agent_runner_client.requests.post")
def test_an_error_event_raises_with_its_message(post):
    post.return_value = _response([_event({"type": "error", "error": {"message": "provider exploded"}}), _exit()])

    with pytest.raises(AgentRunnerError, match="provider exploded"):
        run_sandbox(_call(), SandboxTrace())


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_non_zero_exit_reports_the_last_stderr_lines(post):
    post.return_value = _response(
        [
            _log("stderr", "starting"),
            _log("stderr", "fatal: no such model"),
            _log("stdout", "ignored"),
            _exit(code=2),
        ]
    )

    with pytest.raises(AgentRunnerError) as error:
        run_sandbox(_call(), SandboxTrace())

    assert "exit code 2" in str(error.value)
    assert "fatal: no such model" in str(error.value)


@patch("plane.utils.agent_runner_client.requests.post")
def test_only_the_last_twenty_stderr_lines_are_kept(post):
    lines = [_log("stderr", f"line {index}") for index in range(30)]
    post.return_value = _response([*lines, _exit(code=1)])

    with pytest.raises(AgentRunnerError) as error:
        run_sandbox(_call(), SandboxTrace())

    message = str(error.value)
    assert "line 29" in message
    assert "line 9" not in message


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_timed_out_sandbox_says_it_stopped_producing_output(post):
    post.return_value = _response([_exit(timed_out=True)])

    with pytest.raises(AgentRunnerError, match="stopped producing output"):
        run_sandbox(_call(), SandboxTrace())


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_length_stop_is_reported_as_an_unfinished_answer(post):
    post.return_value = _response([_step_start(), _text("p1", "partial"), _finish(reason="length"), _exit()])

    with pytest.raises(AgentRunnerError, match="stopped before finishing"):
        run_sandbox(_call(), SandboxTrace())


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_stream_without_an_exit_event_raises(post):
    post.return_value = _response([_step_start(), _finish()])

    with pytest.raises(AgentRunnerError, match="before the sandbox exited"):
        run_sandbox(_call(), SandboxTrace())


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_non_200_response_uses_the_runner_error_message(post):
    post.return_value = _response(status_code=400, payload={"error": "invalid workspace"})

    with pytest.raises(AgentRunnerError, match="invalid workspace"):
        run_sandbox(_call(), SandboxTrace())


@patch("plane.utils.agent_runner_client.requests.post")
def test_the_api_key_never_reaches_the_trace_or_an_error(post):
    post.return_value = _response(
        [_step_start(), _text("p1", "done"), _finish(), _exit()],
    )
    trace = SandboxTrace()

    run_sandbox(_call(), trace)

    assert trace.request == {
        "prompt": "Do the work.",
        "model": "opencode-test",
        "effort": "",
        "session_id": "ses_plane_x",
        "read_only": False,
        "title": "ENG-1 develop",
    }
    assert "sk-test" not in json.dumps(trace.request)

    post.return_value = _response(status_code=401, payload={"error": "unauthorized"})
    with pytest.raises(AgentRunnerError) as error:
        run_sandbox(_call(), SandboxTrace())
    assert "sk-test" not in str(error.value)


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_raising_on_progress_closes_the_response(post):
    response = _response([_step_start(), _text("p1", "one"), _finish(), _exit()])
    post.return_value = response

    def stop():
        raise AgentRunnerError("The agent run is no longer active.")

    with pytest.raises(AgentRunnerError):
        run_sandbox(_call(), SandboxTrace(), on_progress=stop)

    response.close.assert_called_once()


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_missing_api_key_is_reported_before_the_call(post):
    call = _call(agent=SimpleNamespace(id="a", name="Agent", api_key="", base_url=""))

    with pytest.raises(AgentRunnerError, match="No API key"):
        run_sandbox(call, SandboxTrace())

    post.assert_not_called()


def test_a_missing_runner_token_is_reported(monkeypatch):
    monkeypatch.delenv("AGENT_RUNNER_TOKEN")

    with pytest.raises(AgentRunnerError, match="not configured"):
        run_sandbox(_call(), SandboxTrace())


@patch("plane.utils.agent_runner_client.requests.post")
def test_the_request_carries_the_models_and_the_workspace(post):
    post.return_value = _response([_exit()])
    call = _call()

    run_sandbox(call, SandboxTrace())

    assert post.call_args.args[0] == "http://runner:9999/v1/sandboxes"
    assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer runner-token"
    body = post.call_args.kwargs["json"]
    assert body["sandbox_key"] == "abc-develop-123"
    assert body["workspace"] == "runs/run/tasks/task"
    assert body["sessions"] == "sessions/issue"
    assert body["session_id"] == "ses_plane_x"
    assert body["model"] == "opencode-test"
    assert body["prompt"] == "Do the work."
    assert body["api_key"] == "sk-test"
    assert body["read_only"] is False
    assert post.call_args.kwargs["stream"] is True


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_connection_failure_becomes_an_agent_runner_error(post):
    post.side_effect = requests.ConnectionError("boom")

    with pytest.raises(AgentRunnerError, match="Could not reach the agent runner"):
        run_sandbox(_call(), SandboxTrace())


@patch("plane.utils.agent_runner_client.requests.post")
def test_a_stream_failure_becomes_an_agent_runner_error_and_closes_the_response(post):
    response = _response([])
    response.iter_lines.side_effect = requests.ConnectionError("cut off")
    post.return_value = response

    with pytest.raises(AgentRunnerError, match="stream failed"):
        run_sandbox(_call(), SandboxTrace())

    response.close.assert_called_once()
