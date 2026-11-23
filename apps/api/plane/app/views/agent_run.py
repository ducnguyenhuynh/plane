# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers.agent import ProjectAgentTeamSerializer
from plane.app.serializers.agent_run import (
    AgentRunDetailSerializer,
    AgentRunSerializer,
    AgentRunSessionDetailSerializer,
    AgentRunSessionSerializer,
)
from plane.bgtasks.agent_pipeline_task import log_event, mark_stale_runs_failed, run_agent_pipeline
from plane.db.models import (
    DEFAULT_AGENT_TEAM,
    AgentRole,
    AgentRun,
    AgentRunSession,
    AgentRunStage,
    AgentRunStatus,
    Issue,
    Project,
    ProjectAgentTeam,
    ProjectAIAgent,
)
from plane.utils.agent_bot import ensure_agent_bot, ensure_project_member

from .base import BaseAPIView


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


class ProjectAgentTeamEndpoint(BaseAPIView):
    def _project(self, slug, project_id):
        return get_object_or_404(Project.objects.filter(workspace__slug=slug), pk=project_id)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id):
        project = self._project(slug, project_id)
        team = ProjectAgentTeam.objects.filter(project=project).first()
        if team:
            return Response(ProjectAgentTeamSerializer(team).data, status=status.HTTP_200_OK)
        # Reading must not write: report the defaults for an unsaved team
        return Response({"id": None, **DEFAULT_AGENT_TEAM}, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def patch(self, request, slug, project_id):
        project = self._project(slug, project_id)
        team = ProjectAgentTeam.objects.filter(project=project).first()
        serializer = ProjectAgentTeamSerializer(team, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        if team:
            serializer.save()
        else:
            serializer.save(project_id=project.id, workspace_id=project.workspace_id)
        return Response(serializer.data, status=status.HTTP_200_OK)


class IssueAgentRunEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        project = get_object_or_404(Project.objects.filter(workspace__slug=slug), pk=project_id)
        mark_stale_runs_failed(project)
        runs = (
            AgentRun.objects.filter(project=project, issue_id=issue_id)
            .select_related("pm_agent", "qa_agent")
            .order_by("-created_at")
        )
        return Response(AgentRunSerializer(runs, many=True).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = get_object_or_404(
            Issue.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=issue_id
        )
        mark_stale_runs_failed(issue.project)
        active = AgentRun.objects.filter(
            project=issue.project,
            issue=issue,
            status__in=[AgentRunStatus.QUEUED, AgentRunStatus.RUNNING],
        )
        if active.exists():
            return Response(
                {"error": "An agent run is already in progress for this work item."},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            return Response(
                {"error": f"Assign an agent to these positions first: {', '.join(missing)}."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Snapshot the model each agent runs with (a position's override wins)
        agent_models = {}
        for position in pm_positions + qa_positions + developer_positions:
            if position.model_override:
                agent_models[str(position.agent_id)] = position.model_override

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
            )
        except IntegrityError:
            # Another request created an active run between the check and the insert
            return Response(
                {"error": "An agent run is already in progress for this work item."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        for position in pm_positions + qa_positions + developer_positions:
            bot = ensure_agent_bot(position.agent)
            ensure_project_member(bot, issue.project)

        run_agent_pipeline.delay(str(run.id))
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)


class AgentRunDetailEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, run_id):
        run = get_object_or_404(
            AgentRun.objects.filter(project__workspace__slug=slug, project_id=project_id)
            .select_related("pm_agent", "qa_agent")
            .prefetch_related("tasks__issue__project", "tasks__agent", "events__agent", "artifacts"),
            pk=run_id,
        )
        return Response(AgentRunDetailSerializer(run).data, status=status.HTTP_200_OK)


class AgentRunSessionListEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, run_id):
        run = get_object_or_404(
            AgentRun.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=run_id
        )
        sessions = run.sessions.defer("request", "response", "reasoning", "usage").order_by("created_at")
        return Response(AgentRunSessionSerializer(sessions, many=True).data, status=status.HTTP_200_OK)


class AgentRunSessionDetailEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, run_id, session_id):
        session = get_object_or_404(
            AgentRunSession.objects.filter(
                run_id=run_id, run__project__workspace__slug=slug, run__project_id=project_id
            ),
            pk=session_id,
        )
        return Response(AgentRunSessionDetailSerializer(session).data, status=status.HTTP_200_OK)


class AgentRunRetryEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, run_id):
        run = get_object_or_404(
            AgentRun.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=run_id
        )
        if run.status != AgentRunStatus.FAILED:
            return Response({"error": "Only failed runs can be retried."}, status=status.HTTP_400_BAD_REQUEST)

        run.status = AgentRunStatus.QUEUED
        run.error = ""
        run.completed_at = None
        # A run with tasks resumes at the developer stage; otherwise the PM plan is regenerated
        run.stage = AgentRunStage.DEVELOPER if run.tasks.exists() else AgentRunStage.PM
        run.save(update_fields=["status", "error", "completed_at", "stage", "updated_at"])
        run_agent_pipeline.delay(str(run.id))
        return Response(AgentRunSerializer(run).data, status=status.HTTP_200_OK)


class AgentRunCancelEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, run_id):
        run = get_object_or_404(
            AgentRun.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=run_id
        )
        if run.status not in (AgentRunStatus.QUEUED, AgentRunStatus.RUNNING):
            return Response(
                {"error": "Only queued or running runs can be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        display_name = request.user.display_name or request.user.email or "a member"
        run.status = AgentRunStatus.FAILED
        run.error = f"Cancelled by {display_name}."
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error", "completed_at", "updated_at"])
        log_event(run, "status", run.error)
        return Response(AgentRunSerializer(run).data, status=status.HTTP_200_OK)
