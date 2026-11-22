# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import requests

# Third party imports
from django.db import IntegrityError
from django.shortcuts import get_object_or_404
from openai import APIConnectionError, APIStatusError, AuthenticationError, NotFoundError, OpenAI
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers import AIAgentSerializer, ProjectAIAgentSerializer
from plane.db.models import AgentProvider, AIAgent, Project, ProjectAIAgent, Workspace
from plane.license.utils.encryption import decrypt_data
from plane.utils.exception_logger import log_exception

from .base import BaseAPIView

ANTHROPIC_VERSION = "2023-06-01"


def provider_headers(agent: AIAgent) -> dict:
    """OpenCode Go requires a stable session id per conversation and a client user agent."""
    if agent.base_url and "opencode.ai" in agent.base_url:
        return {"x-opencode-session": str(agent.id), "user-agent": "plane-ai-agent/1.0"}
    return {}


def provider_error_detail(response) -> str:
    """Surface the provider's own message (e.g. low credits) without dumping the whole payload."""
    try:
        payload = response.json()
        error = payload.get("error")
        message = error.get("message") if isinstance(error, dict) else None
        message = message or payload.get("message") or ""
    except Exception:
        message = ""
    return f" {str(message).strip()[:240]}" if message else ""


def check_agent_connection(agent: AIAgent) -> tuple[bool, str]:
    """Send one minimal request to the provider so bad credentials surface in the UI, not later."""
    api_key = decrypt_data(agent.api_key)
    if not api_key:
        return False, "No API key configured for this agent."

    try:
        if agent.provider == AgentProvider.ANTHROPIC:
            base_url = (agent.base_url or "https://api.anthropic.com").rstrip("/")
            payload = {
                "model": agent.model,
                "max_tokens": 16,
                "messages": [{"role": "user", "content": "ping"}],
            }
            if agent.reasoning_effort:
                payload["output_config"] = {"effort": agent.reasoning_effort}
            response = requests.post(
                f"{base_url}/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "content-type": "application/json",
                    **provider_headers(agent),
                },
                json=payload,
                timeout=15,
            )
            if response.status_code == status.HTTP_200_OK:
                return True, "Connection successful."
            if response.status_code in (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN):
                return False, "Authentication failed. Check the API key."
            if response.status_code == status.HTTP_404_NOT_FOUND:
                return False, "Model not found. Check the model name or base URL."
            return (
                False,
                f"The provider rejected the request (HTTP {response.status_code}).{provider_error_detail(response)}",
            )

        client = OpenAI(
            api_key=api_key,
            base_url=agent.base_url or None,
            timeout=15.0,
            default_headers=provider_headers(agent),
        )
        create_kwargs = {
            "model": agent.model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 16,
        }
        if agent.reasoning_effort:
            create_kwargs["reasoning_effort"] = agent.reasoning_effort
        client.chat.completions.create(**create_kwargs)
        return True, "Connection successful."
    except Exception as e:
        log_exception(e)
        if isinstance(e, AuthenticationError):
            return False, "Authentication failed. Check the API key."
        if isinstance(e, NotFoundError):
            return False, "Model not found. Check the model name or base URL."
        if isinstance(e, APIStatusError):
            error = getattr(e, "body", None) or {}
            detail = ""
            if isinstance(error, dict):
                error_value = error.get("error")
                if isinstance(error_value, dict):
                    detail = error_value.get("message") or ""
                elif isinstance(error_value, str):
                    detail = error_value
            detail = detail or str(getattr(e, "message", ""))
            return False, (
                f"The provider rejected the request (HTTP {e.status_code}). {str(detail).strip()[:240]}"
            ).strip()
        if isinstance(e, APIConnectionError):
            return False, "Could not reach the provider. Check the base URL and network access."
        return False, "The connection test failed. Check the API logs for details."


class AIAgentEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug, pk=None):
        agents = AIAgent.objects.filter(workspace__slug=slug)
        if pk:
            return Response(AIAgentSerializer(get_object_or_404(agents, pk=pk)).data, status=status.HTTP_200_OK)
        return Response(AIAgentSerializer(agents, many=True).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        workspace = Workspace.objects.get(slug=slug)
        serializer = AIAgentSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            serializer.save(workspace_id=workspace.id)
        except IntegrityError:
            return Response(
                {"name": "An agent with this name already exists in the workspace."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def patch(self, request, slug, pk):
        agent = get_object_or_404(AIAgent.objects.filter(workspace__slug=slug), pk=pk)
        serializer = AIAgentSerializer(agent, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        try:
            serializer.save()
        except IntegrityError:
            return Response(
                {"name": "An agent with this name already exists in the workspace."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug, pk):
        get_object_or_404(AIAgent.objects.filter(workspace__slug=slug), pk=pk).delete()
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
        assignments = ProjectAIAgent.objects.filter(
            project__workspace__slug=slug, project_id=project_id
        ).select_related("agent")
        if pk:
            assignments = assignments.filter(pk=pk)
        return Response(ProjectAIAgentSerializer(assignments, many=True).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def post(self, request, slug, project_id):
        project = self._project(slug, project_id)
        serializer = ProjectAIAgentSerializer(data=request.data, context={"project": project})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save(project_id=project.id, workspace_id=project.workspace_id)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def patch(self, request, slug, project_id, pk):
        assignment = get_object_or_404(
            ProjectAIAgent.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=pk
        )
        serializer = ProjectAIAgentSerializer(
            assignment, data=request.data, partial=True, context={"project": assignment.project}
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def delete(self, request, slug, project_id, pk):
        get_object_or_404(
            ProjectAIAgent.objects.filter(project__workspace__slug=slug, project_id=project_id), pk=pk
        ).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
