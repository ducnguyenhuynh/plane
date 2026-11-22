# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest
from rest_framework import status

from plane.db.models import AgentRole, AIAgent, Project, ProjectAIAgent, ProjectMember, Workspace, WorkspaceMember
from plane.license.utils.encryption import decrypt_data
from plane.tests.factories import UserFactory


@pytest.mark.contract
class TestAIAgentEndpoint:
    @pytest.mark.django_db
    def test_create_agent_encrypts_key_and_never_returns_it(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/ai-agents/"

        response = session_client.post(
            url,
            {"name": "Opus", "provider": "anthropic", "model": "claude-opus-5.5", "api_key": "sk-secret"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert "api_key" not in response.data
        assert response.data["has_api_key"] is True
        agent = AIAgent.objects.get(pk=response.data["id"])
        assert agent.api_key != "sk-secret"
        assert decrypt_data(agent.api_key) == "sk-secret"

    @pytest.mark.django_db
    def test_api_key_is_not_required_on_update(self, session_client, workspace):
        agent = AIAgent.objects.create(
            workspace=workspace, name="Opus", provider="anthropic", model="old", api_key="encrypted"
        )
        url = f"/api/workspaces/{workspace.slug}/ai-agents/{agent.id}/"

        response = session_client.patch(url, {"model": "new"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        agent.refresh_from_db()
        assert agent.model == "new"
        assert agent.api_key == "encrypted"

    @pytest.mark.django_db
    def test_member_can_read_but_not_create(self, api_client, workspace):
        member = UserFactory()
        WorkspaceMember.objects.create(workspace=workspace, member=member, role=15)
        api_client.force_authenticate(user=member)

        url = f"/api/workspaces/{workspace.slug}/ai-agents/"
        assert api_client.get(url).status_code == status.HTTP_200_OK
        response = api_client.post(
            url, {"name": "Dev", "provider": "openai", "model": "gpt", "api_key": "sk"}, format="json"
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.django_db
    def test_custom_provider_requires_base_url(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/ai-agents/"

        response = session_client.post(
            url, {"name": "Dev", "provider": "custom", "model": "deepseek-4.1-flash", "api_key": "sk"}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "base_url" in response.data


@pytest.mark.contract
class TestProjectAIAgentEndpoint:
    @pytest.fixture
    def project(self, workspace):
        project = Project.objects.create(name="Test Project", identifier="TP", workspace=workspace)
        ProjectMember.objects.create(project=project, workspace=workspace, member=workspace.owner, role=20)
        return project

    @pytest.fixture
    def agent(self, workspace):
        return AIAgent.objects.create(workspace=workspace, name="Opus", provider="anthropic", model="claude-opus-5.5")

    @pytest.mark.django_db
    def test_assign_agent_to_position(self, session_client, workspace, project, agent):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/"

        response = session_client.post(
            url,
            {"agent_id": str(agent.id), "role": AgentRole.REVIEWER, "model_override": "claude-haiku"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["agent"]["id"] == str(agent.id)
        assert response.data["role"] == AgentRole.REVIEWER
        assert ProjectAIAgent.objects.filter(project=project, agent=agent).exists()

    @pytest.mark.django_db
    def test_duplicate_position_is_rejected(self, session_client, workspace, project, agent):
        ProjectAIAgent.objects.create(project=project, agent=agent, role=AgentRole.QA)
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/"

        response = session_client.post(url, {"agent_id": str(agent.id), "role": AgentRole.QA}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.django_db
    def test_agent_from_another_workspace_is_rejected(self, session_client, workspace, project):
        other_workspace = Workspace.objects.create(name="Other", owner=workspace.owner, slug="other-workspace")
        foreign_agent = AIAgent.objects.create(
            workspace=other_workspace, name="Foreign", provider="openai", model="gpt", api_key="sk"
        )
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/"

        response = session_client.post(
            url, {"agent_id": str(foreign_agent.id), "role": AgentRole.DEVELOPER}, format="json"
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "agent_id" in response.data
