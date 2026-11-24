# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Starting agent runs: from the API and from an issue comment mention."""

# Django imports
from django.db import IntegrityError, transaction

# Module imports
from plane.app.permissions import ROLE
from plane.bgtasks.agent_pipeline_task import (
    enqueue_agent_run,
    issue_identifier,
    mark_stale_runs_failed,
    post_comment,
)
from plane.db.models import (
    DEFAULT_AGENT_TEAM,
    AgentRole,
    AgentRun,
    AgentRunStatus,
    AgentRunTask,
    ProjectAIAgent,
    ProjectAgentTeam,
    ProjectMember,
)
from plane.utils.agent_bot import ensure_agent_bot, ensure_project_member
from plane.utils.exception_logger import log_exception

ACTIVE_STATUSES = [AgentRunStatus.QUEUED, AgentRunStatus.RUNNING]
ALREADY_RUNNING = "An agent run is already in progress for this work item."
BUSY_MESSAGE = "An agent run is already in progress on this work item. Mention the agent again when it finishes."


class AgentRunStartError(Exception):
    pass


def project_positions(project):
    return (
        ProjectAIAgent.objects.filter(project=project, is_active=True, agent__is_active=True)
        .select_related("agent")
        .order_by("created_at")
    )


def team_limits(project):
    team = ProjectAgentTeam.objects.filter(project=project).first()
    if not team:
        return dict(DEFAULT_AGENT_TEAM)
    return {
        "pm_count": team.pm_count,
        "qa_count": team.qa_count,
        "developer_count": team.developer_count,
    }


def start_agent_run(issue, *, instructions="", trigger_comment=None) -> AgentRun:
    """Snapshot the project team into a new queued run."""
    mark_stale_runs_failed(issue.project)
    if AgentRun.objects.filter(project=issue.project, issue=issue, status__in=ACTIVE_STATUSES).exists():
        raise AgentRunStartError(ALREADY_RUNNING)

    limits = team_limits(issue.project)
    positions = project_positions(issue.project)
    pm_positions = list(positions.filter(role=AgentRole.PROJECT_MANAGER)[: limits["pm_count"]])
    qa_positions = list(positions.filter(role=AgentRole.QA)[: limits["qa_count"]])
    developer_positions = list(positions.filter(role=AgentRole.DEVELOPER)[: limits["developer_count"]])

    missing = []
    if not pm_positions:
        missing.append("project manager")
    if not developer_positions:
        missing.append("developer")
    if not qa_positions:
        missing.append("QA")
    if missing:
        raise AgentRunStartError(f"Assign an agent to these positions first: {', '.join(missing)}.")

    # Snapshot the model each agent runs with (a position's override wins)
    agent_models = {}
    for position in pm_positions + qa_positions + developer_positions:
        if position.model_override:
            agent_models[str(position.agent_id)] = position.model_override

    parent_run = AgentRun.objects.filter(issue=issue).first()
    try:
        run = AgentRun.objects.create(
            project=issue.project,
            workspace=issue.project.workspace,
            issue=issue,
            pm_agent=pm_positions[0].agent,
            pm_agents=[str(position.agent_id) for position in pm_positions],
            developer_agents=[str(position.agent_id) for position in developer_positions],
            qa_agent=qa_positions[0].agent,
            qa_agents=[str(position.agent_id) for position in qa_positions],
            agent_models=agent_models,
            parent_run=parent_run,
            instructions=instructions or "",
            trigger_comment=trigger_comment,
        )
    except IntegrityError as error:
        # Another request created an active run between the check and the insert
        raise AgentRunStartError(ALREADY_RUNNING) from error

    for position in pm_positions + qa_positions + developer_positions:
        bot = ensure_agent_bot(position.agent)
        ensure_project_member(bot, issue.project)

    transaction.on_commit(lambda: enqueue_agent_run(str(run.id)))
    return run


def mentioned_bot_ids(comment):
    """The bot users of this project's agents that the comment mentions."""
    from plane.bgtasks.notification_task import extract_comment_mentions

    identifiers = extract_comment_mentions(comment.comment_html or "")
    bot_ids = {
        str(bot_id)
        for bot_id in ProjectAIAgent.objects.filter(
            project_id=comment.project_id, is_active=True, agent__is_active=True
        ).values_list("agent__bot_user_id", flat=True)
        if bot_id
    }
    return {identifier for identifier in identifiers if str(identifier) in bot_ids}


def handle_comment_followup(comment, user) -> None:
    """Start a run, or answer that one is busy, when a member mentions an agent. Never raises."""
    try:
        if user is None or getattr(user, "is_bot", False):
            return

        membership = ProjectMember.objects.filter(project_id=comment.project_id, member=user, is_active=True).first()
        if membership is None or membership.role < ROLE.MEMBER.value:
            return

        if not mentioned_bot_ids(comment):
            return

        issue = comment.issue
        # Agents post on subtasks, so a mention there continues the parent work item
        task = (
            AgentRunTask.objects.filter(issue=issue)
            .select_related("issue__project", "run")
            .order_by("-created_at")
            .first()
        )
        target = task.run.issue if task is not None else issue
        instructions = comment.comment_stripped or ""
        if task is not None:
            instructions = f"For subtask {issue_identifier(task.issue)} {task.issue.name}: {instructions}"

        active = AgentRun.objects.filter(
            project_id=comment.project_id, issue=target, status__in=ACTIVE_STATUSES
        ).first()
        if active is not None:
            bot = active.pm_agent.bot_user if active.pm_agent else None
            if bot is not None:
                post_comment(active, issue, bot, BUSY_MESSAGE, kind=f"busy:{comment.id}")
            return

        try:
            start_agent_run(target, instructions=instructions, trigger_comment=comment)
        except AgentRunStartError as e:
            log_exception(e)
    except Exception as e:
        log_exception(e)
