# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
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
from plane.bgtasks.agent_pipeline_task import log_event, mark_stale_runs_failed
from plane.db.models import (
    DEFAULT_AGENT_ALLOWED_HOSTS,
    DEFAULT_AGENT_TEAM,
    AgentRun,
    AgentRunSession,
    AgentRunStage,
    AgentRunStatus,
    Issue,
    Project,
    ProjectAgentTeam,
)
from plane.utils.agent_followup import AgentRunStartError, enqueue_agent_run, start_agent_run

from .base import BaseAPIView


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
        return Response(
            {
                "id": None,
                **DEFAULT_AGENT_TEAM,
                "allowed_hosts": [],
                "websearch_enabled": False,
                "webfetch_enabled": False,
                "skills": [],
                "mcp_servers": [],
                "default_allowed_hosts": DEFAULT_AGENT_ALLOWED_HOSTS,
            },
            status=status.HTTP_200_OK,
        )

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
        instructions = request.data.get("instructions")
        if not isinstance(instructions, str):
            instructions = ""
        try:
            run = start_agent_run(issue, instructions=instructions)
        except AgentRunStartError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
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
        sessions = run.sessions.defer("request", "response", "reasoning", "usage", "tool_calls").order_by("created_at")
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
        enqueue_agent_run(str(run.id))
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
