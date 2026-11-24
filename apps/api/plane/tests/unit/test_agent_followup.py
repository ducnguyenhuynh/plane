# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from plane.app.permissions import ROLE
from plane.db.models import DEFAULT_AGENT_TEAM, AgentRole
from plane.utils.agent_followup import AgentRunStartError, handle_comment_followup, mentioned_bot_ids, start_agent_run

BOT_ID = str(uuid4())
MODULE = "plane.utils.agent_followup"


def _mention_html():
    return (
        '<p><mention-component entity_name="user_mention" '
        f'entity_identifier="{BOT_ID}"></mention-component> please fix this</p>'
    )


def _comment(html=None):
    return SimpleNamespace(
        id=uuid4(),
        project_id=uuid4(),
        comment_html=_mention_html() if html is None else html,
        comment_stripped="please fix this",
        issue=SimpleNamespace(),
    )


def _user(is_bot=False):
    return SimpleNamespace(id=uuid4(), is_bot=is_bot)


def _members(role=ROLE.MEMBER.value):
    members = MagicMock()
    members.objects.filter.return_value.first.return_value = SimpleNamespace(role=role)
    return members


def _agents():
    agents = MagicMock()
    agents.objects.filter.return_value.values_list.return_value = [BOT_ID]
    return agents


def _tasks(result=None):
    tasks = MagicMock()
    tasks.objects.filter.return_value.select_related.return_value.order_by.return_value.first.return_value = result
    return tasks


def test_bots_are_ignored():
    with (
        patch(f"{MODULE}.start_agent_run") as start,
        patch(f"{MODULE}.ProjectMember", side_effect=AssertionError("must not touch the database")),
    ):
        handle_comment_followup(_comment(), _user(is_bot=True))

    start.assert_not_called()


def test_guests_are_ignored():
    with (
        patch(f"{MODULE}.ProjectMember", _members(ROLE.GUEST.value)),
        patch(f"{MODULE}.start_agent_run") as start,
        patch(f"{MODULE}.mentioned_bot_ids", side_effect=AssertionError("must not parse mentions")),
    ):
        handle_comment_followup(_comment(), _user())

    start.assert_not_called()


def test_comments_without_an_agent_mention_are_ignored():
    with (
        patch(f"{MODULE}.ProjectMember", _members()),
        patch(f"{MODULE}.ProjectAIAgent", _agents()),
        patch(f"{MODULE}.start_agent_run") as start,
    ):
        handle_comment_followup(_comment(html="<p>no mention here</p>"), _user())

    start.assert_not_called()


def test_a_mention_during_an_active_run_posts_one_busy_reply():
    comment = _comment()
    bot = SimpleNamespace(id=uuid4())
    active = SimpleNamespace(pm_agent=SimpleNamespace(bot_user=bot))
    runs = MagicMock()
    runs.objects.filter.return_value.first.return_value = active

    with (
        patch(f"{MODULE}.ProjectMember", _members()),
        patch(f"{MODULE}.ProjectAIAgent", _agents()),
        patch(f"{MODULE}.AgentRunTask", _tasks()),
        patch(f"{MODULE}.AgentRun", runs),
        patch(f"{MODULE}.post_comment") as post,
        patch(f"{MODULE}.start_agent_run") as start,
    ):
        handle_comment_followup(comment, _user())

    start.assert_not_called()
    assert post.call_args.args[:3] == (active, comment.issue, bot)
    assert "in progress" in post.call_args.args[3]
    assert post.call_args.kwargs["kind"] == f"busy:{comment.id}"


def test_a_mention_on_a_subtask_continues_the_parent_work_item():
    comment = _comment()
    parent = SimpleNamespace()
    task = SimpleNamespace(
        issue=SimpleNamespace(project=SimpleNamespace(identifier="ENG"), sequence_id=12, name="Fix it"),
        run=SimpleNamespace(issue=parent),
    )
    runs = MagicMock()
    runs.objects.filter.return_value.first.return_value = None

    with (
        patch(f"{MODULE}.ProjectMember", _members()),
        patch(f"{MODULE}.ProjectAIAgent", _agents()),
        patch(f"{MODULE}.AgentRunTask", _tasks(task)),
        patch(f"{MODULE}.AgentRun", runs),
        patch(f"{MODULE}.start_agent_run") as start,
    ):
        handle_comment_followup(comment, _user())

    assert runs.objects.filter.call_args.kwargs["issue"] is parent
    start.assert_called_once_with(
        parent,
        instructions="For subtask ENG-12 Fix it: please fix this",
        trigger_comment=comment,
    )


def test_a_mention_starts_a_follow_up_run():
    comment = _comment()
    runs = MagicMock()
    runs.objects.filter.return_value.first.return_value = None

    with (
        patch(f"{MODULE}.ProjectMember", _members()),
        patch(f"{MODULE}.ProjectAIAgent", _agents()),
        patch(f"{MODULE}.AgentRunTask", _tasks()),
        patch(f"{MODULE}.AgentRun", runs),
        patch(f"{MODULE}.start_agent_run") as start,
    ):
        handle_comment_followup(comment, _user())

    start.assert_called_once_with(comment.issue, instructions="please fix this", trigger_comment=comment)


def test_it_never_raises():
    with patch(f"{MODULE}.ProjectMember") as members:
        members.objects.filter.side_effect = RuntimeError("the database is down")

        handle_comment_followup(_comment(), _user())


def test_mentioned_bot_ids_reads_the_comment_html():
    with patch(f"{MODULE}.ProjectAIAgent", _agents()):
        assert mentioned_bot_ids(_comment()) == {BOT_ID}


def test_a_run_cannot_start_on_a_work_item_created_by_a_non_member():
    issue = SimpleNamespace(project=SimpleNamespace(), created_by_id=uuid4())
    runs = MagicMock()
    runs.objects.filter.return_value.exists.return_value = False

    with (
        patch(f"{MODULE}.mark_stale_runs_failed"),
        patch(f"{MODULE}.AgentRun", runs),
        patch(f"{MODULE}.ProjectMember") as members,
    ):
        members.objects.filter.return_value.exists.return_value = False

        with pytest.raises(AgentRunStartError, match="created by project members"):
            start_agent_run(issue)

    assert members.objects.filter.call_args.kwargs == {
        "project": issue.project,
        "member_id": issue.created_by_id,
        "is_active": True,
        "role__gte": ROLE.MEMBER.value,
    }
    runs.objects.create.assert_not_called()


class _Positions:
    def __init__(self, by_role):
        self.by_role = by_role

    def filter(self, role):
        return self.by_role[role]


def _position(agent, override=""):
    return SimpleNamespace(agent=agent, agent_id=uuid4(), model_override=override)


def _start_run(designer_positions):
    project = SimpleNamespace(workspace=SimpleNamespace(), workspace_id=uuid4())
    issue = SimpleNamespace(project=project, created_by_id=uuid4())
    agents = {name: SimpleNamespace(name=name, model="m") for name in ("PM", "Dev", "QA", "Designer")}
    positions = _Positions(
        {
            AgentRole.PROJECT_MANAGER: [_position(agents["PM"])],
            AgentRole.QA: [_position(agents["QA"])],
            AgentRole.DEVELOPER: [_position(agents["Dev"])],
            AgentRole.DESIGNER: designer_positions,
        }
    )
    runs = MagicMock()

    with (
        patch(f"{MODULE}.mark_stale_runs_failed"),
        patch(f"{MODULE}.AgentRun", runs),
        patch(f"{MODULE}.ProjectMember") as members,
        patch(f"{MODULE}.team_limits", return_value=dict(DEFAULT_AGENT_TEAM)),
        patch(f"{MODULE}.project_positions", return_value=positions),
        patch(f"{MODULE}.ensure_agent_bot"),
        patch(f"{MODULE}.ensure_project_member"),
        patch(f"{MODULE}.transaction.on_commit"),
    ):
        members.objects.filter.return_value.exists.return_value = True
        runs.objects.filter.return_value.exists.return_value = False
        start_agent_run(issue)

    return runs.objects.create.call_args.kwargs


def test_a_run_starts_without_a_designer_position():
    kwargs = _start_run([])

    assert kwargs["designer_agents"] == []


def test_a_run_snapshots_designers_and_their_model_override():
    designer = SimpleNamespace(name="Designer", model="m")
    position = _position(designer, "opencode-design")

    kwargs = _start_run([position])

    assert kwargs["designer_agents"] == [str(position.agent_id)]
    assert kwargs["agent_models"][str(position.agent_id)] == "opencode-design"


def test_comment_creation_never_fails_because_of_agents():
    from plane.app.views.issue.comment import _comment_followup

    with (
        patch("plane.db.models.IssueComment.objects.filter", side_effect=RuntimeError("the database is down")),
        patch("plane.app.views.issue.comment.log_exception") as log,
    ):
        _comment_followup(uuid4(), uuid4())

    log.assert_called_once()
