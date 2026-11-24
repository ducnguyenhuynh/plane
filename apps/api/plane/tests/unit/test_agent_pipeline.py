# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import re
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from django.utils import timezone

from plane.bgtasks.agent_pipeline_task import (
    STALE_QUEUED_HOURS,
    STALE_RUN_MINUTES,
    AgentRuntimeError,
    QaRejectedError,
    agent_session_id,
    comment_key,
    find_session_scope,
    followup_context,
    group_tasks_by_agent,
    human_comments,
    invoke,
    is_cancelled,
    latest_review_notes,
    mark_stale_agent_runs,
    mark_stale_runs_failed,
    plan_tasks,
    plan_tasks_from,
    prepare_context,
    progress_callback,
    run_final_stage,
    run_qa_stage,
    sandbox_settings,
    select_plan,
    start_session,
    work_item_data,
)
from plane.app.permissions import ROLE
from plane.db.models import AGENT_WEBSEARCH_HOST, DEFAULT_AGENT_ALLOWED_HOSTS, AgentRunStatus
from plane.utils.agent_git import BranchDivergedError
from plane.utils.agent_runner_client import SandboxTrace

PM_A = SimpleNamespace(id="a", name="PM A")
PM_B = SimpleNamespace(id="b", name="PM B")


def _plan(count):
    return '{"tasks": [' + ", ".join(f'{{"title": "Task {index}"}}' for index in range(count)) + "]}"


def _run():
    return SimpleNamespace(
        pk="run",
        id=uuid4(),
        issue_id=uuid4(),
        project_id=uuid4(),
        workspace_id=uuid4(),
        started_at="started",
        save=MagicMock(),
        stage="developer",
        rework_round=0,
        agent_models={},
        issue=SimpleNamespace(
            project=SimpleNamespace(identifier="ENG"),
            sequence_id=1,
            name="Parent",
            description_stripped="",
            priority="medium",
        ),
        trigger_comment_id=None,
        instructions="",
        parent_run=None,
        pull_request_url="",
    )


def _agent():
    return SimpleNamespace(
        id=uuid4(),
        name="Agent",
        provider="opencode",
        model="opencode-test",
        reasoning_effort="",
        system_prompt="",
        api_key="encrypted",
    )


def _ctx():
    workspace = MagicMock()
    workspace.relative.side_effect = lambda path: str(path)
    workspace.prepare_sessions_dir.side_effect = lambda name: f"/sessions/{name}"
    return SimpleNamespace(workspace=workspace, repo=None, sandbox={})


def test_plan_tasks_from_ignores_non_objects_and_caps_the_count():
    plan = '{"tasks": [{"title": "One"}, "nope", {"title": "Two"}, {"title": "Three"}]}'

    assert [task["title"] for task in plan_tasks_from(plan, 2)] == ["One", "Two"]


def test_plan_tasks_from_returns_nothing_without_tasks():
    assert plan_tasks_from("not json", 3) == []
    assert plan_tasks_from('{"tasks": "nope"}', 3) == []


def test_plan_tasks_from_normalizes_the_role():
    tasks = plan_tasks_from('{"tasks": [{"role": "designer"}, {"role": "Designer"}, {}]}', 10)

    assert [task["role"] for task in tasks] == ["designer", "developer", "developer"]


def test_plan_tasks_from_keeps_paths_depends_on_and_subtask():
    plan = (
        '{"tasks": [{"title": "One", "paths": ["src/app.py", 5], "depends_on": [0, "x", true], '
        '"subtask": "ENG-12"}, {"title": "Two", "paths": "nope", "depends_on": 3, "subtask": 7}]}'
    )

    tasks = plan_tasks_from(plan, 10)

    assert tasks[0]["paths"] == ["src/app.py"]
    assert tasks[0]["depends_on"] == [0]
    assert tasks[0]["subtask"] == "ENG-12"
    assert tasks[1]["paths"] == []
    assert tasks[1]["depends_on"] == []
    assert tasks[1]["subtask"] == ""


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


def _plan_run():
    run = _run()
    run.project = SimpleNamespace()
    run.tasks = MagicMock()
    run.tasks.exists.return_value = False
    return run


def _created_tasks(run, plan, agents, designers=()):
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(**kwargs)

    with (
        patch("plane.bgtasks.agent_pipeline_task.Issue") as issues,
        patch("plane.bgtasks.agent_pipeline_task.AgentRunTask") as tasks,
        patch("plane.bgtasks.agent_pipeline_task.ensure_agent_bot"),
        patch("plane.bgtasks.agent_pipeline_task.ensure_project_member"),
        patch(
            "plane.bgtasks.agent_pipeline_task.create_child_issue",
            side_effect=lambda *args: SimpleNamespace(id=uuid4()),
        ),
    ):
        issues.objects.filter.return_value = []
        tasks.objects.create.side_effect = create
        plan_tasks(run, plan, agents, "pm", designers=designers)

    return created


def test_a_designer_subtask_goes_to_a_designer():
    developer, designer = _agent(), _agent()

    created = _created_tasks(_plan_run(), [{"title": "Design", "role": "designer"}], [developer], [designer])

    assert created[0]["agent"] is designer
    assert created[0]["role"] == "designer"


def test_a_designer_subtask_without_designers_falls_back_to_a_developer():
    developer = _agent()

    created = _created_tasks(_plan_run(), [{"title": "Design", "role": "designer"}], [developer])

    assert created[0]["agent"] is developer
    assert created[0]["role"] == "developer"


def test_a_reused_subtask_keeps_its_developer_and_the_developer_prompt():
    developer, designer = _agent(), _agent()
    child = SimpleNamespace(id=uuid4(), sequence_id=7)
    created = []

    def create(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(**kwargs)

    with (
        patch("plane.bgtasks.agent_pipeline_task.Issue") as issues,
        patch("plane.bgtasks.agent_pipeline_task.AgentRunTask") as tasks,
        patch("plane.bgtasks.agent_pipeline_task.create_child_issue") as create_child,
    ):
        issues.objects.filter.return_value = [child]
        tasks.objects.filter.return_value.order_by.return_value.first.return_value = SimpleNamespace(
            agent_id=developer.id
        )
        tasks.objects.create.side_effect = create
        plan_tasks(
            _plan_run(), [{"title": "Polish", "role": "designer", "subtask": "ENG-7"}], [developer], "pm", [designer]
        )

    create_child.assert_not_called()
    assert created[0]["agent"] is developer
    assert created[0]["role"] == "developer"


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


def test_followup_context_carries_the_previous_run_and_the_new_request():
    run = _run()
    run.parent_run = SimpleNamespace(
        result="Done",
        branch="plane/ENG-1",
        pull_request_url="https://github.com/acme/app/pull/1",
    )
    run.instructions = "Also add tests."

    context = followup_context(run)

    assert "Done" in context
    assert "plane/ENG-1" in context
    assert "https://github.com/acme/app/pull/1" in context
    assert "New request:\nAlso add tests." in context


def test_followup_context_has_no_comments_section():
    run = _run()
    run.parent_run = SimpleNamespace(result="Done", branch="", pull_request_url="")

    # Comments already appear once, wrapped as data, in <team_comments>
    assert "Human comments" not in followup_context(run)


def test_human_comments_send_no_email_address_and_keep_the_newest():
    run = _run()
    rows = MagicMock()
    rows.__iter__.return_value = iter([("Ann", "fix it")])

    with (
        patch("plane.bgtasks.agent_pipeline_task.IssueComment") as comments,
        patch("plane.bgtasks.agent_pipeline_task.ProjectMember") as members,
    ):
        queryset = comments.objects.filter.return_value.order_by.return_value
        queryset.values_list.return_value = rows

        assert human_comments(run) == [("Ann", "fix it")]

    # Only active project members may reach a model
    members.objects.filter.assert_called_once_with(
        project_id=run.project_id, is_active=True, role__gte=ROLE.MEMBER.value
    )
    comment_filter = comments.objects.filter.call_args.kwargs
    assert comment_filter["actor_id__in"] is members.objects.filter.return_value.values.return_value
    assert comment_filter["actor__is_bot"] is False
    queryset.values_list.assert_called_once_with("actor__display_name", "comment_stripped")
    # No slice: the newest comments must not be dropped
    rows.__getitem__.assert_not_called()


def test_work_item_data_labels_a_missing_name_as_a_member():
    run = _run()

    with patch("plane.bgtasks.agent_pipeline_task.human_comments", return_value=[("", "fix it")]):
        assert "A member: fix it" in work_item_data(run)


def test_queued_runs_are_only_stale_after_a_day():
    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
    ):
        runs.objects.filter.return_value.update.return_value = 2
        sessions.objects.filter.return_value.update.return_value = 0

        assert mark_stale_runs_failed("project") == 2

    expression = runs.objects.filter.call_args.args[0]
    running, queued = (dict(child.children) for child in expression.children)
    now = timezone.now()
    assert running["status"] == AgentRunStatus.RUNNING
    assert queued["status"] == AgentRunStatus.QUEUED
    # A running run goes stale in minutes, a queued one may wait a day for a worker
    assert (now - running["updated_at__lt"]).total_seconds() == pytest.approx(STALE_RUN_MINUTES * 60, abs=60)
    assert (now - queued["updated_at__lt"]).total_seconds() == pytest.approx(STALE_QUEUED_HOURS * 3600, abs=60)
    # The dead sessions are cleaned up too
    sessions.objects.filter.return_value.update.assert_called_once()


def test_the_stale_check_covers_every_project_without_a_scope():
    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
    ):
        runs.objects.filter.return_value.update.return_value = 1
        sessions.objects.filter.return_value.update.return_value = 0

        assert mark_stale_runs_failed() == 1

    assert "project" not in runs.objects.filter.call_args.kwargs
    assert "run__project" not in sessions.objects.filter.call_args.kwargs


def test_the_periodic_task_fails_stale_runs_everywhere():
    with patch("plane.bgtasks.agent_pipeline_task.mark_stale_runs_failed") as stale:
        mark_stale_agent_runs()

    stale.assert_called_once_with()


def test_followup_context_is_empty_for_a_first_run():
    assert followup_context(_run()) == ""


def test_latest_review_notes_only_returns_rejected_reviews_of_the_round():
    run = _run()
    reviews = [
        SimpleNamespace(content="approved", data={"passed": True, "round": 0}),
        SimpleNamespace(content="old rejection", data={"passed": False, "round": 1}),
        SimpleNamespace(content="missing tests", data={"passed": False, "round": 0}),
    ]
    run.artifacts = SimpleNamespace(
        filter=lambda **kwargs: SimpleNamespace(order_by=lambda *args: reviews),
    )

    assert latest_review_notes(run) == "missing tests"


def _patch_logging():
    return (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession"),
        patch("plane.bgtasks.agent_pipeline_task.AgentRun"),
    )


def test_agent_session_id_is_deterministic_and_bounded():
    issue_id, agent_id = uuid4(), uuid4()
    session_id = agent_session_id(issue_id, agent_id, "pm")

    assert session_id == agent_session_id(issue_id, agent_id, "pm")
    assert len(session_id) == 50
    assert re.fullmatch(r"ses_plane_[0-9a-f]{40}", session_id)
    assert session_id != agent_session_id(uuid4(), agent_id, "pm")
    assert session_id != agent_session_id(issue_id, uuid4(), "pm")
    assert session_id != agent_session_id(issue_id, agent_id, "qa")


def test_a_reset_generation_changes_the_session_id_only_from_one_on():
    issue_id, agent_id = uuid4(), uuid4()

    # Generation 0 is the id every session had before resets existed
    assert agent_session_id(issue_id, agent_id, "pm", 0) == agent_session_id(issue_id, agent_id, "pm")
    assert agent_session_id(issue_id, agent_id, "pm", 1) != agent_session_id(issue_id, agent_id, "pm")


def test_find_session_scope_recovers_pm_qa_and_task_scopes():
    issue_id, agent_id, task_issue_id = uuid4(), uuid4(), uuid4()

    for scope in ("pm", "qa", task_issue_id.hex):
        session_id = agent_session_id(issue_id, agent_id, scope)
        assert find_session_scope(issue_id, agent_id, session_id, [task_issue_id]) == scope
    assert find_session_scope(issue_id, agent_id, "ses_other", [task_issue_id]) == ""


def test_start_session_stores_the_prompt_and_session_id():
    run, agent = _run(), _agent()

    with patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions:
        sessions.objects.create.return_value = SimpleNamespace(pk="session")

        start_session(run, agent, "", "plan", None, "do it\x00", "ses_plane_x")

    kwargs = sessions.objects.create.call_args.kwargs
    assert kwargs["request"] == {"prompt": "do it", "session_id": "ses_plane_x"}
    assert kwargs["session_id"] == "ses_plane_x"


@patch("plane.bgtasks.agent_pipeline_task.run_sandbox", return_value="ok")
def test_invoke_builds_the_sandbox_call(run_sandbox):
    run, agent = _run(), _agent()
    agent.system_prompt = "Be brief."
    task = SimpleNamespace(id=uuid4(), issue_id=uuid4(), save=MagicMock())

    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
        patch("plane.bgtasks.agent_pipeline_task.AgentSessionReset") as resets,
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
    ):
        resets.objects.filter.return_value.count.return_value = 0
        sessions.objects.create.return_value = SimpleNamespace(pk="session", started_at=None)
        runs.objects.filter.return_value.update.return_value = 1

        assert (
            invoke(
                run,
                _ctx(),
                agent,
                "Do the work.",
                purpose="develop_retry",
                scope="scopehex",
                work_dir="/work",
                read_only=False,
                task=task,
            )
            == "ok"
        )

    call = run_sandbox.call_args.args[0]
    assert call.model == "opencode-test"
    assert call.session_id == agent_session_id(run.issue_id, agent.id, "scopehex")
    assert call.sandbox_key.startswith(f"{run.id.hex[:12]}-develop-retry-")
    assert "_" not in call.sandbox_key
    assert call.title == "ENG-1 develop_retry"
    assert call.workspace == "/work"
    assert call.sessions.startswith("/sessions/")
    assert call.read_only is False
    assert call.prompt.startswith("Agent instructions:\nBe brief.")
    assert task.session_id == call.session_id
    task.save.assert_called_with(update_fields=["session_id", "updated_at"])


@patch("plane.bgtasks.agent_pipeline_task.run_sandbox", return_value="ok")
def test_the_agent_model_override_wins(run_sandbox):
    run, agent = _run(), _agent()
    run.agent_models = {str(agent.id): "opencode-fast"}

    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
        patch("plane.bgtasks.agent_pipeline_task.AgentSessionReset"),
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
    ):
        sessions.objects.create.return_value = SimpleNamespace(pk="session", started_at=None)
        runs.objects.filter.return_value.update.return_value = 1

        invoke(run, _ctx(), agent, "hi", purpose="plan", scope="pm", work_dir="/work", read_only=True)

    assert run_sandbox.call_args.args[0].model == "opencode-fast"


def test_a_session_log_failure_does_not_fail_invoke():
    run, agent = _run(), _agent()

    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
        patch("plane.bgtasks.agent_pipeline_task.AgentSessionReset"),
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
        patch("plane.bgtasks.agent_pipeline_task.run_sandbox", return_value="ok"),
    ):
        sessions.objects.create.side_effect = Exception("the log table is down")
        runs.objects.filter.return_value.update.return_value = 1

        assert invoke(run, _ctx(), agent, "hi", purpose="plan", scope="pm", work_dir="/work", read_only=True) == "ok"
        # Without a row there is nothing to flush or finish
        sessions.objects.filter.assert_not_called()


def test_a_heartbeat_with_no_updated_rows_raises_inside_on_progress():
    run, agent = _run(), _agent()

    def fake_sandbox(call, trace, on_progress=None):
        on_progress()
        return "ok"

    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
        patch("plane.bgtasks.agent_pipeline_task.AgentSessionReset"),
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
        patch("plane.bgtasks.agent_pipeline_task.run_sandbox", side_effect=fake_sandbox),
    ):
        sessions.objects.create.return_value = SimpleNamespace(pk="session", started_at=None)
        # The run was cancelled or marked stale between two heartbeats
        runs.objects.filter.return_value.update.return_value = 0

        with pytest.raises(AgentRuntimeError):
            invoke(run, _ctx(), agent, "hi", purpose="plan", scope="pm", work_dir="/work", read_only=True)


def test_the_progress_callback_writes_the_request_on_its_first_flush_only():
    run = _run()
    trace = SandboxTrace(
        request={"prompt": "first", "session_id": "ses_x"},
        text="hello",
        reasoning="why",
        tool_calls=[{"tool": "bash"}],
        usage={"input_tokens": 3},
        cost=Decimal("0.25"),
    )

    with (
        patch("plane.bgtasks.agent_pipeline_task.AgentRunSession") as sessions,
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
    ):
        runs.objects.filter.return_value.update.return_value = 1
        on_progress = progress_callback(run, SimpleNamespace(pk="session"), trace)
        on_progress()

        # A later flush must not overwrite the prompt already stored
        trace.request = {"prompt": "second", "session_id": "ses_x"}
        on_progress()

    flushes = [call.kwargs for call in sessions.objects.filter.return_value.update.call_args_list]
    assert flushes[0]["request"] == {"prompt": "first", "session_id": "ses_x"}
    assert flushes[0]["cost"] == Decimal("0.25")
    assert flushes[0]["tool_calls"] == [{"tool": "bash"}]
    assert flushes[0]["usage"] == {"input_tokens": 3}
    assert "request" not in flushes[1]


def test_an_empty_review_is_not_counted_as_a_rejection():
    run, agent = _run(), _agent()
    run.qa_agent = None
    run.save = MagicMock()

    with (
        patch("plane.bgtasks.agent_pipeline_task.reviewer_agents", return_value=[agent]),
        patch("plane.bgtasks.agent_pipeline_task.human_comments", return_value=[]),
        patch("plane.bgtasks.agent_pipeline_task.AgentRunArtifact") as artifacts,
        patch("plane.bgtasks.agent_pipeline_task.log_event"),
        patch("plane.bgtasks.agent_pipeline_task.prepare_review_dir", return_value="/review"),
        patch("plane.bgtasks.agent_pipeline_task.invoke", return_value="   "),
    ):
        with pytest.raises(AgentRuntimeError, match="No QA reviewer produced a review"):
            run_qa_stage(run, _ctx(), [])

    artifacts.objects.create.assert_not_called()


def test_a_string_false_verdict_is_not_an_approval():
    run, agent = _run(), _agent()
    run.qa_agent = None
    run.save = MagicMock()

    with (
        patch("plane.bgtasks.agent_pipeline_task.reviewer_agents", return_value=[agent]),
        patch("plane.bgtasks.agent_pipeline_task.human_comments", return_value=[]),
        patch("plane.bgtasks.agent_pipeline_task.AgentRunArtifact") as artifacts,
        patch("plane.bgtasks.agent_pipeline_task.post_comment"),
        patch("plane.bgtasks.agent_pipeline_task.ensure_agent_bot"),
        patch("plane.bgtasks.agent_pipeline_task.log_event"),
        patch("plane.bgtasks.agent_pipeline_task.prepare_review_dir", return_value="/review"),
        patch("plane.bgtasks.agent_pipeline_task.invoke", return_value='{"passed": "false", "notes": "nope"}'),
    ):
        with pytest.raises(QaRejectedError):
            run_qa_stage(run, _ctx(), [])

    assert artifacts.objects.create.call_args.kwargs["data"]["passed"] is False


def test_a_replaced_job_counts_as_cancelled():
    run = _run()

    with patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs:
        runs.objects.filter.return_value.exclude.return_value.exists.return_value = False

        assert is_cancelled(run) is True

    assert runs.objects.filter.call_args.kwargs == {"pk": run.pk, "started_at": run.started_at}
    runs.objects.filter.return_value.exclude.assert_called_once_with(
        status__in=[AgentRunStatus.FAILED, AgentRunStatus.QUEUED]
    )


def _prepare_diverged_context(run, repo):
    integration = SimpleNamespace(github_repository="acme/app", github_default_branch="main")

    with (
        patch("plane.bgtasks.agent_pipeline_task.ProjectAgentTeam") as teams,
        patch("plane.bgtasks.agent_pipeline_task.ProjectIntegration") as integrations,
        patch("plane.bgtasks.agent_pipeline_task.GitHubConnection") as connections,
        patch("plane.bgtasks.agent_pipeline_task.decrypt_data", return_value="token"),
        patch("plane.bgtasks.agent_pipeline_task.AgentWorkspace"),
        patch("plane.bgtasks.agent_pipeline_task.AgentRepository", return_value=repo),
        patch("plane.bgtasks.agent_pipeline_task.save_run"),
        patch("plane.bgtasks.agent_pipeline_task.log_event"),
    ):
        teams.objects.filter.return_value.first.return_value = None
        integrations.objects.filter.return_value.exclude.return_value.first.return_value = integration
        connections.objects.filter.return_value.first.return_value = SimpleNamespace(token="encrypted")
        return prepare_context(run, resume=True)


def test_a_new_run_after_a_failed_parent_starts_again_from_github():
    run = _run()
    run.tasks = MagicMock()
    run.tasks.exists.return_value = False
    repo = MagicMock()
    repo.ensure_branch.side_effect = [BranchDivergedError("diverged"), ("head", "base")]

    ctx = _prepare_diverged_context(run, repo)

    assert ctx.branch == "plane/ENG-1"
    assert repo.ensure_branch.call_args_list[0].kwargs["resume"] is True
    # The unpushed work of the failed run is gone, so the branch restarts from GitHub
    assert repo.ensure_branch.call_args_list[1].kwargs["resume"] is False


def test_a_real_retry_re_raises_a_diverged_branch():
    run = _run()
    run.tasks = MagicMock()
    run.tasks.exists.return_value = True
    repo = MagicMock()
    repo.ensure_branch.side_effect = BranchDivergedError("diverged")

    with pytest.raises(BranchDivergedError):
        _prepare_diverged_context(run, repo)

    assert repo.ensure_branch.call_count == 1


def test_the_final_stage_never_overwrites_a_cancel():
    run, ctx, agent = _run(), _ctx(), _agent()
    run.artifacts = MagicMock()
    run.artifacts.filter.return_value.order_by.return_value.first.return_value = None
    run.artifacts.filter.return_value.order_by.return_value.__getitem__.return_value = []

    with (
        patch("plane.bgtasks.agent_pipeline_task.invoke", return_value="final text"),
        patch("plane.bgtasks.agent_pipeline_task.human_comments", return_value=[]),
        patch("plane.bgtasks.agent_pipeline_task.log_event"),
        patch("plane.bgtasks.agent_pipeline_task.is_cancelled", return_value=True),
        patch("plane.bgtasks.agent_pipeline_task.post_comment") as post,
        patch("plane.bgtasks.agent_pipeline_task.AgentRunArtifact") as artifacts,
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
    ):
        run_final_stage(run, ctx, [], agent)

    # Cancelled right after the final answer: nothing is written or posted
    runs.objects.filter.assert_not_called()
    artifacts.objects.create.assert_not_called()
    post.assert_not_called()

    with (
        patch("plane.bgtasks.agent_pipeline_task.invoke", return_value="final text"),
        patch("plane.bgtasks.agent_pipeline_task.human_comments", return_value=[]),
        patch("plane.bgtasks.agent_pipeline_task.log_event"),
        patch("plane.bgtasks.agent_pipeline_task.is_cancelled", return_value=False),
        patch("plane.bgtasks.agent_pipeline_task.post_comment"),
        patch("plane.bgtasks.agent_pipeline_task.AgentRunArtifact"),
        patch("plane.bgtasks.agent_pipeline_task.AgentRun") as runs,
        patch("plane.bgtasks.agent_pipeline_task.ensure_agent_bot"),
    ):
        run_final_stage(run, ctx, [], agent)

    assert runs.objects.filter.call_args.kwargs == {
        "pk": run.pk,
        "status": AgentRunStatus.RUNNING,
        "started_at": run.started_at,
    }
    updates = runs.objects.filter.return_value.update.call_args.kwargs
    assert updates["status"] == AgentRunStatus.COMPLETED
    assert updates["result"] == "final text"


def test_sandbox_settings_merge_hosts_and_decrypt_mcp_headers():
    run = _run()
    enabled_skill = {"name": "review", "description": "d", "content": "c", "enabled": True}
    team = SimpleNamespace(
        allowed_hosts=["example.com"],
        websearch_enabled=True,
        webfetch_enabled=False,
        skills=[enabled_skill, {"name": "off", "description": "d", "content": "c", "enabled": False}],
        mcp_servers=[
            {
                "name": "tools",
                "url": "https://mcp.example.com/mcp",
                "enabled": True,
                "headers": {"Authorization": "encrypted"},
            },
            {"name": "off", "url": "https://mcp.example.com/x", "enabled": False, "headers": {}},
        ],
    )
    workspace = MagicMock()
    workspace.relative.side_effect = lambda path: str(path)
    workspace.prepare_cache_dir.return_value = "/cache/project"
    workspace.prepare_skills_dir.return_value = "/skills/run"

    with (
        patch("plane.bgtasks.agent_pipeline_task.ProjectAgentTeam") as teams,
        patch("plane.bgtasks.agent_pipeline_task.decrypt_data", return_value="plain-secret") as decrypt,
    ):
        teams.objects.filter.return_value.first.return_value = team
        settings = sandbox_settings(run, workspace)

    assert settings["allowed_hosts"][: len(DEFAULT_AGENT_ALLOWED_HOSTS)] == tuple(DEFAULT_AGENT_ALLOWED_HOSTS)
    assert "example.com" in settings["allowed_hosts"]
    assert AGENT_WEBSEARCH_HOST in settings["allowed_hosts"]
    assert settings["websearch"] is True
    assert settings["webfetch"] is False
    assert settings["cache"] == "/cache/project"
    assert settings["skills"] == "/skills/run"
    assert settings["mcp_servers"] == (
        {"name": "tools", "url": "https://mcp.example.com/mcp", "headers": {"Authorization": "plain-secret"}},
    )
    decrypt.assert_called_once_with("encrypted")
    assert workspace.prepare_skills_dir.call_args.args[1] == [enabled_skill]


def test_qa_is_writable_but_cannot_edit_source_files():
    run, agent = _run(), _agent()
    run.qa_agent = None
    run.tasks = MagicMock()

    with (
        patch("plane.bgtasks.agent_pipeline_task.reviewer_agents", return_value=[agent]),
        patch("plane.bgtasks.agent_pipeline_task.human_comments", return_value=[]),
        patch("plane.bgtasks.agent_pipeline_task.AgentRunArtifact"),
        patch("plane.bgtasks.agent_pipeline_task.log_event"),
        patch("plane.bgtasks.agent_pipeline_task.prepare_review_dir", return_value="/review"),
        patch("plane.bgtasks.agent_pipeline_task.invoke", return_value='{"passed": true}') as invoke_mock,
        patch("plane.bgtasks.agent_pipeline_task.post_comment"),
        patch("plane.bgtasks.agent_pipeline_task.ensure_agent_bot"),
        patch("plane.bgtasks.agent_pipeline_task.move_task_state"),
    ):
        run_qa_stage(run, _ctx(), [])

    kwargs = invoke_mock.call_args.kwargs
    assert kwargs["read_only"] is False
    assert kwargs["deny_edit"] is True
