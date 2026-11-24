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
            {"name": "Opus", "provider": "opencode", "model": "deepseek-v4.1-flash", "api_key": "sk-secret"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert "api_key" not in response.data
        assert response.data["has_api_key"] is True
        agent = AIAgent.objects.get(pk=response.data["id"])
        assert agent.api_key != "sk-secret"
        assert decrypt_data(agent.api_key) == "sk-secret"
        # A missing base URL becomes the OpenCode default
        assert agent.base_url == "https://opencode.ai/inference/go/v1"

    @pytest.mark.django_db
    def test_api_key_is_not_required_on_update(self, session_client, workspace):
        agent = AIAgent.objects.create(
            workspace=workspace,
            name="Opus",
            provider="opencode",
            model="old",
            api_key="encrypted",
            base_url="https://opencode.ai/inference/go/v1",
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
    def test_only_opencode_providers_and_endpoints_are_accepted(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/ai-agents/"

        foreign_provider = session_client.post(
            url, {"name": "Dev", "provider": "custom", "model": "deepseek-4.1-flash", "api_key": "sk"}, format="json"
        )
        foreign_base_url = session_client.post(
            url,
            {
                "name": "Dev",
                "provider": "opencode",
                "model": "deepseek-4.1-flash",
                "api_key": "sk",
                "base_url": "https://api.openai.com/v1",
            },
            format="json",
        )

        assert foreign_provider.status_code == status.HTTP_400_BAD_REQUEST
        assert "provider" in foreign_provider.data
        assert foreign_base_url.status_code == status.HTTP_400_BAD_REQUEST
        assert "base_url" in foreign_base_url.data


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
            {"agent_id": str(agent.id), "role": AgentRole.DEVELOPER, "model_override": "claude-haiku"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert str(response.data["agent"]["id"]) == str(agent.id)
        assert response.data["role"] == AgentRole.DEVELOPER
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

    @pytest.mark.django_db
    def test_reviewer_position_is_rejected(self, session_client, workspace, project, agent):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/"

        response = session_client.post(url, {"agent_id": str(agent.id), "role": AgentRole.REVIEWER}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["role"] == ["The reviewer position is not used by agent runs yet."]
        assert not ProjectAIAgent.objects.filter(project=project, agent=agent).exists()

    @pytest.mark.django_db
    def test_a_removed_position_can_be_assigned_again(self, session_client, workspace, project, agent):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/"
        created = session_client.post(url, {"agent_id": str(agent.id), "role": AgentRole.DEVELOPER}, format="json")
        assert created.status_code == status.HTTP_201_CREATED
        removed = session_client.delete(f"{url}{created.data['id']}/")
        assert removed.status_code == status.HTTP_204_NO_CONTENT

        response = session_client.post(url, {"agent_id": str(agent.id), "role": AgentRole.DEVELOPER}, format="json")

        assert response.status_code == status.HTTP_201_CREATED

    @pytest.mark.django_db
    def test_assigning_a_position_creates_the_agent_memberships(self, session_client, workspace, project, agent):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/"

        response = session_client.post(url, {"agent_id": str(agent.id), "role": AgentRole.DEVELOPER}, format="json")

        assert response.status_code == status.HTTP_201_CREATED
        agent.refresh_from_db()
        assert agent.bot_user_id is not None
        assert agent.bot_user.is_bot is True
        assert WorkspaceMember.objects.filter(workspace=workspace, member=agent.bot_user, is_active=True).exists()
        assert ProjectMember.objects.filter(project=project, member=agent.bot_user, is_active=True).exists()

    @pytest.mark.django_db
    def test_removing_the_last_position_deactivates_the_project_member(self, session_client, workspace, project, agent):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/"
        created = session_client.post(url, {"agent_id": str(agent.id), "role": AgentRole.DEVELOPER}, format="json")
        agent.refresh_from_db()

        session_client.delete(f"{url}{created.data['id']}/")

        assert not ProjectMember.objects.filter(project=project, member=agent.bot_user, is_active=True).exists()

    @pytest.mark.django_db
    def test_positions_expose_the_in_team_flag(self, session_client, workspace, project, agent):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/"
        others = [
            AIAgent.objects.create(workspace=workspace, name=f"Dev {index}", provider="openai", model="gpt")
            for index in range(3)
        ]
        ProjectAIAgent.objects.create(project=project, agent=agent, role=AgentRole.DEVELOPER)
        for other in others:
            ProjectAIAgent.objects.create(project=project, agent=other, role=AgentRole.DEVELOPER)

        response = session_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        # The default team is three developers
        assert [position["in_team"] for position in response.data] == [True, True, True, False]

    @pytest.mark.django_db
    def test_members_only_see_the_lite_agent(self, api_client, workspace, project):
        agent = AIAgent.objects.create(
            workspace=workspace,
            name="Opus",
            provider="anthropic",
            model="claude-opus-5.5",
            api_key="encrypted",
            base_url="https://internal.example.com",
            system_prompt="secret instructions",
        )
        ProjectAIAgent.objects.create(project=project, agent=agent, role=AgentRole.DEVELOPER)
        member = UserFactory()
        WorkspaceMember.objects.create(workspace=workspace, member=member, role=15)
        ProjectMember.objects.create(project=project, workspace=workspace, member=member, role=15)
        api_client.force_authenticate(user=member)

        response = api_client.get(f"/api/workspaces/{workspace.slug}/ai-agents/")
        positions = api_client.get(f"/api/workspaces/{workspace.slug}/projects/{project.id}/ai-agents/")

        assert response.status_code == status.HTTP_200_OK
        assert set(response.data[0]) == {"id", "name", "provider", "model", "is_active", "bot_user_id"}
        assert positions.status_code == status.HTTP_200_OK
        assert set(positions.data[0]["agent"]) == {"id", "name", "provider", "model", "is_active", "bot_user_id"}
        assert "system_prompt" not in positions.data[0]["agent"]

    @pytest.mark.django_db
    def test_admins_see_the_full_agent(self, session_client, workspace):
        AIAgent.objects.create(
            workspace=workspace,
            name="Opus",
            provider="anthropic",
            model="claude-opus-5.5",
            api_key="encrypted",
            system_prompt="secret instructions",
        )

        response = session_client.get(f"/api/workspaces/{workspace.slug}/ai-agents/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data[0]["system_prompt"] == "secret instructions"
        assert response.data[0]["has_api_key"] is True
