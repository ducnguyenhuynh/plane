# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from plane.bgtasks.agent_pipeline_task import (
    AgentRuntimeError,
    comment_key,
    group_tasks_by_agent,
    invoke,
    plan_tasks_from,
    progress_callback,
    select_plan,
    start_session,
)
from plane.utils.agent_runtime import AgentCallTrace

PM_A = SimpleNamespace(id="a", name="PM A")
PM_B = SimpleNamespace(id="b", name="PM B")


def _plan(count):
    return '{"tasks": [' + ", ".join(f'{{"title": "Task {index}"}}' for index in range(count)) + "]}"


def test_plan_tasks_from_ignores_non_objects_and_caps_the_count():
    plan = '{"tasks": [{"title": "One"}, "nope", {"title": "Two"}, {"title": "Three"}]}'

    assert [task["title"] for task in plan_tasks_from(plan, 2)] == ["One", "Two"]


def test_plan_tasks_from_returns_nothing_without_tasks():
    assert plan_tasks_from("not json", 3) == []
    assert plan_tasks_from('{"tasks": "nope"}', 3) == []


def test_select_plan_picks_the_plan_with_the_most_valid_tasks():
    candidates = [(PM_A, _plan(1)), (PM_B, _plan(3))]

    agent, text, tasks = select_plan(candidates, 10)

    assert agent is PM_B
    assert len(tasks) == 3
    assert text == _plan(3)


def test_select_plan_ties_go_to_the_lead_pm():
    candidates = [(PM_A, _plan(2)), (PM_B, _plan(2))]

    assert select_plan(candidates, 10)[0] is PM_A


def test_select_plan_scores_are_capped_by_the_limit():
    candidates = [(PM_A, _plan(5)), (PM_B, _plan(3))]

    # Both fill the limit of 3, so the lead PM wins
    assert select_plan(candidates, 3)[0] is PM_A


def test_group_tasks_by_agent_keeps_one_group_per_agent_in_first_seen_order():
    dev_a = SimpleNamespace(name="Dev A")
    dev_b = SimpleNamespace(name="Dev B")
    tasks = [
        SimpleNamespace(agent_id="a", agent=dev_a, name="one"),
        SimpleNamespace(agent_id="b", agent=dev_b, name="two"),
        SimpleNamespace(agent_id="a", agent=dev_a, name="three"),
    ]

    groups = group_tasks_by_agent(tasks)

    assert [group[0] for group in groups] == [dev_a, dev_b]
    assert [[task.name for task in group[1]] for group in groups] == [["one", "three"], ["two"]]


def test_comment_key_is_stable_for_the_same_run_kind_task_and_round():
    assert comment_key("run", "plan", None, 0) == comment_key("run", "plan", None, 0)


def test_comment_key_changes_with_every_part_of_the_identity():
    base = comment_key("run", "plan", None, 0)
    assert base != comment_key("other", "plan", None, 0)
    assert base != comment_key("run", "final", None, 0)
    assert base != comment_key("run", "plan", "task", 0)
    assert base != comment_key("run", "plan", None, 1)


def test_comment_key_uses_a_placeholder_for_comments_without_a_task():
    assert comment_key("run", "plan", None, 0).endswith(":none:0")


def _run_and_agent():
    run = SimpleNamespace(pk="run", stage="developer", rework_round=0, agent_models={})
    agent = SimpleNamespace(id="agent", name="Agent", provider="anthropic", model="m", reasoning_effort="")
    return run, agent


def test_a_session_log_failure_does_not_fail_invoke():
    run, agent = _run_and_agent()

    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
        patch("plane.bgtasks.agent_pipeline_task.invoke_agent", return_value="ok"),
    ):
        sessions.objects.create.side_effect = Exception("the log table is down")
        runs.objects.filter.return_value.update.return_value = 1

        assert invoke(run, agent, [], purpose="plan") == "ok"
        # Without a row there is nothing to flush or finish
        sessions.objects.filter.assert_not_called()


def test_a_heartbeat_with_no_updated_rows_raises_inside_on_progress():
    run, agent = _run_and_agent()

    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
        patch("plane.bgtasks.agent_pipeline_task.invoke_agent", return_value="ok") as call,
    ):
        sessions.objects.create.return_value = SimpleNamespace(pk="session")
        runs.objects.filter.return_value.update.return_value = 1

        invoke(run, agent, [])

        on_progress = call.call_args.kwargs["on_progress"]
        on_progress()

        # The run was cancelled or marked stale between two chunks
        runs.objects.filter.return_value.update.return_value = 0
        with pytest.raises(AgentRuntimeError):
            on_progress()


def test_start_session_stores_the_messages():
    run, agent = _run_and_agent()

    with patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions:
        sessions.objects.create.return_value = SimpleNamespace(pk="session")

        start_session(run, agent, "", "plan", None, [{"role": "user", "content": "hi\x00"}])

    assert sessions.objects.create.call_args.kwargs["request"] == {"messages": [{"role": "user", "content": "hi"}]}


def test_the_progress_callback_writes_the_request_on_its_first_flush_only():
    run, _ = _run_and_agent()
    trace = AgentCallTrace(request={"messages": [{"role": "user", "content": "first"}]}, max_tokens=4096)

    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
    ):
        runs.objects.filter.return_value.update.return_value = 1
        on_progress = progress_callback(run, SimpleNamespace(pk="session"), trace)
        on_progress()

        # A later resend must not overwrite the prompt already stored
        trace.request = {"messages": [{"role": "user", "content": "second"}]}
        on_progress()

    flushes = [call.kwargs for call in sessions.objects.filter.return_value.update.call_args_list]
    assert flushes[0]["request"] == {"messages": [{"role": "user", "content": "first"}]}
    assert flushes[0]["max_tokens"] == 4096
    assert "request" not in flushes[1]
    assert "max_tokens" not in flushes[1]
