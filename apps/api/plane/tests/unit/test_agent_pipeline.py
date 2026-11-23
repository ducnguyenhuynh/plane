# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from types import SimpleNamespace

from plane.bgtasks.agent_pipeline_task import (
    comment_key,
    group_tasks_by_agent,
    plan_tasks_from,
    select_plan,
)

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
