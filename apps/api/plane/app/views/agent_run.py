# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers.agent import ProjectAgentTeamSerializer
from plane.app.serializers.agent_run import AgentRunDetailSerializer, AgentRunSerializer
from plane.bgtasks.agent_pipeline_task import run_agent_pipeline
from plane.db.models import (
    AgentRole,
    AgentRun,
    AgentRunStage,
    AgentRunStatus,
    Issue,
    Project,
    ProjectAgentTeam,
    ProjectAIAgent,
)

from .base import BaseAPIView

DEFAULT_LIMITS = {"pm_count": 1, "qa_count": 1, "developer_count": 3}


def project_positions(project):
    return ProjectAIAgent.objects.filter(project=project, is_active=True).select_related("agent").order_by("created_at")


def team_limits(project):
    team = ProjectAgentTeam.objects.filter(project=project).first()
    if not team:
        return DEFAULT_LIMITS
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
        team, _ = ProjectAgentTeam.objects.get_or_create(
            project=project, defaults={"workspace_id": project.workspace_id}
        )
        return Response(ProjectAgentTeamSerializer(team).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def patch(self, request, slug, project_id):
        project = self._project(slug, project_id)
        team, _ = ProjectAgentTeam.objects.get_or_create(
            project=project, defaults={"workspace_id": project.workspace_id}
        )
        serializer = ProjectAgentTeamSerializer(team, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class IssueAgentRunEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, issue_id):
        runs = (
            AgentRun.objects.filter(project__workspace__slug=slug, project_id=project_id, issue_id=issue_id)
            .select_related("pm_agent", "qa_agent")
            .order_by("-created_at")
        )
        return Response(AgentRunSerializer(runs, many=True).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def post(self, request, slug, project_id, issue_id):
        issue = get_object_or_404(
            Issue.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=issue_id
        )
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

        run = AgentRun.objects.create(
            project=issue.project,
            workspace=issue.project.workspace,
            issue=issue,
            pm_agent=pm_positions[0].agent,
            pm_agents=[str(position.agent_id) for position in pm_positions],
            developer_agents=[str(position.agent_id) for position in developer_positions],
            qa_agent=qa_positions[0].agent,
            qa_agents=[str(position.agent_id) for position in qa_positions],
        )
        run_agent_pipeline.delay(str(run.id))
        return Response(AgentRunSerializer(run).data, status=status.HTTP_201_CREATED)


class AgentRunDetailEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, run_id):
        run = get_object_or_404(
            AgentRun.objects.filter(project__workspace__slug=slug, project_id=project_id).select_related(
                "pm_agent", "qa_agent"
            ),
            pk=run_id,
        )
        return Response(AgentRunDetailSerializer(run).data, status=status.HTTP_200_OK)


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
