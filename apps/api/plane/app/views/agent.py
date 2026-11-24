# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import AIAgentLiteSerializer, AIAgentSerializer, ProjectAIAgentSerializer
from plane.db.models import (
    AgentRole,
    AIAgent,
    Project,
    ProjectAIAgent,
    Workspace,
    WorkspaceMember,
)
from plane.utils.agent_bot import (
    deactivate_agent_bot,
    deactivate_project_member,
    ensure_agent_bot,
    ensure_project_member,
    sync_agent_bot_name,
)
from plane.utils.agent_followup import team_limits
from plane.utils.agent_runtime import check_agent_connection

from .base import BaseAPIView


def is_workspace_admin(request, slug) -> bool:
    return WorkspaceMember.objects.filter(
        workspace__slug=slug, member=request.user, role=ROLE.ADMIN.value, is_active=True
    ).exists()


class AIAgentEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, pk=None):
        agents = AIAgent.objects.filter(workspace__slug=slug).select_related("bot_user")
        serializer = AIAgentSerializer if is_workspace_admin(request, slug) else AIAgentLiteSerializer
        if pk:
            return Response(
                serializer(get_object_or_404(agents, pk=pk)).data,
                status=status.HTTP_200_OK,
            )
        return Response(serializer(agents, many=True).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        serializer = AIAgentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            agent = serializer.save(workspace_id=workspace.id)
        except IntegrityError:
            return Response(
                {"name": "An agent with this name already exists in the workspace."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ensure_agent_bot(agent)
        return Response(AIAgentSerializer(agent).data, status=status.HTTP_201_CREATED)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, pk):
        agent = get_object_or_404(AIAgent.objects.filter(workspace__slug=slug), pk=pk)
        serializer = AIAgentSerializer(agent, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            agent = serializer.save()
        except IntegrityError:
            return Response(
                {"name": "An agent with this name already exists in the workspace."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        sync_agent_bot_name(agent)
        return Response(AIAgentSerializer(agent).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, pk):
        agent = get_object_or_404(AIAgent.objects.filter(workspace__slug=slug), pk=pk)
        deactivate_agent_bot(agent)
        agent.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AIAgentTestEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug, pk):
        agent = get_object_or_404(AIAgent.objects.filter(workspace__slug=slug), pk=pk)
        ok, message = check_agent_connection(agent)
        return Response({"ok": ok, "message": message}, status=status.HTTP_200_OK)


class ProjectAIAgentEndpoint(BaseAPIView):
    def _project(self, slug, project_id):
        return get_object_or_404(Project.objects.filter(workspace__slug=slug), pk=project_id)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id, pk=None):
        project = self._project(slug, project_id)
        assignments = list(
            ProjectAIAgent.objects.filter(project=project)
            .select_related("agent", "agent__bot_user")
            .order_by("created_at")
        )

        # T7: a position is "in the team" when it is among the first N active ones of its role
        limits = team_limits(project)
        roles = {
            AgentRole.PROJECT_MANAGER: limits["pm_count"],
            AgentRole.QA: limits["qa_count"],
            AgentRole.DEVELOPER: limits["developer_count"],
            AgentRole.DESIGNER: limits["designer_count"],
        }
        in_team = set()
        for role, limit in roles.items():
            active = [item for item in assignments if item.role == role and item.is_active]
            in_team.update(item.id for item in active[:limit])
        for assignment in assignments:
            assignment.in_team = assignment.id in in_team

        if pk:
            assignments = [assignment for assignment in assignments if str(assignment.id) == str(pk)]
        serializer = ProjectAIAgentSerializer(
            assignments,
            many=True,
            context={"project": project, "is_admin": is_workspace_admin(request, slug)},
        )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def post(self, request, slug, project_id):
        project = self._project(slug, project_id)
        serializer = ProjectAIAgentSerializer(data=request.data, context={"project": project})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            assignment = serializer.save(project_id=project.id, workspace_id=project.workspace_id)
        except IntegrityError:
            return Response(
                {"role": "This agent already has that position on the project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        ensure_agent_bot(assignment.agent)
        ensure_project_member(assignment.agent.bot_user, project)
        return Response(
            ProjectAIAgentSerializer(assignment, context={"is_admin": is_workspace_admin(request, slug)}).data,
            status=status.HTTP_201_CREATED,
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def patch(self, request, slug, project_id, pk):
        assignment = get_object_or_404(
            ProjectAIAgent.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=pk
        )
        # A position that is switched off leaves the project team
        was_active = assignment.is_active
        serializer = ProjectAIAgentSerializer(
            assignment,
            data=request.data,
            partial=True,
            context={"project": assignment.project, "is_admin": is_workspace_admin(request, slug)},
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            assignment = serializer.save()
        except IntegrityError:
            return Response(
                {"role": "This agent already has that position on the project."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        bot = ensure_agent_bot(assignment.agent)
        if assignment.is_active:
            ensure_project_member(bot, assignment.project)
        elif (
            was_active
            and not ProjectAIAgent.objects.filter(
                project=assignment.project, agent=assignment.agent, is_active=True
            ).exists()
        ):
            deactivate_project_member(bot, assignment.project)
        return Response(
            ProjectAIAgentSerializer(assignment, context={"is_admin": is_workspace_admin(request, slug)}).data,
            status=status.HTTP_200_OK,
        )

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def delete(self, request, slug, project_id, pk):
        assignment = get_object_or_404(
            ProjectAIAgent.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=pk
        )
        project = assignment.project
        agent = assignment.agent
        assignment.delete()
        # The agent leaves the project once it has no position left there
        if (
            not ProjectAIAgent.objects.filter(project=project, agent=agent, is_active=True).exists()
            and agent.bot_user_id
        ):
            deactivate_project_member(agent.bot_user, project)
        return Response(status=status.HTTP_204_NO_CONTENT)
