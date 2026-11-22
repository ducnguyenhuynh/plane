# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import patch

import pytest
from rest_framework import status

from plane.db.models import (
    AIAgent,
    AgentRole,
    AgentRun,
    AgentRunStage,
    AgentRunStatus,
    AgentRunTask,
    Issue,
    Project,
    ProjectAIAgent,
    ProjectMember,
)


@pytest.mark.contract
class TestAgentRunEndpoint:
    @pytest.fixture
    def project(self, workspace):
        project = Project.objects.create(name="Agent Project", identifier="AP", workspace=workspace)
        ProjectMember.objects.create(project=project, workspace=workspace, member=workspace.owner, role=20)
        return project

    @pytest.fixture
    def positions(self, workspace, project):
        agents = {
            role: AIAgent.objects.create(
                workspace=workspace, name=f"{role} agent", provider="openai", model="gpt-test", api_key="encrypted"
            )
            for role in (AgentRole.PROJECT_MANAGER, AgentRole.DEVELOPER, AgentRole.QA)
        }
        for role, agent in agents.items():
            ProjectAIAgent.objects.create(project=project, agent=agent, role=role)
        return agents

    @pytest.fixture
    def issue(self, workspace, project):
        return Issue.objects.create(name="Build the thing", project=project, workspace=workspace)

    def url(self, workspace, project, issue):
        return f"/api/workspaces/{workspace.slug}/projects/{project.id}/issues/{issue.id}/agent-runs/"

    @pytest.mark.django_db
    @patch("plane.app.views.agent_run.run_agent_pipeline.delay")
    def test_start_run_snapshots_the_project_positions(
        self, mock_delay, session_client, workspace, project, positions, issue
    ):
        response = session_client.post(self.url(workspace, project, issue), format="json")

        assert response.status_code == status.HTTP_201_CREATED
        run = AgentRun.objects.get(issue=issue)
        assert run.status == AgentRunStatus.QUEUED
        assert run.pm_agent_id == positions[AgentRole.PROJECT_MANAGER].id
        assert run.pm_agents == [str(positions[AgentRole.PROJECT_MANAGER].id)]
        assert run.qa_agent_id == positions[AgentRole.QA].id
        assert run.qa_agents == [str(positions[AgentRole.QA].id)]
        assert run.developer_agents == [str(positions[AgentRole.DEVELOPER].id)]
        mock_delay.assert_called_once_with(str(run.id))

    @pytest.mark.django_db
    def test_start_requires_every_position(self, session_client, workspace, project, issue):
        pm = AIAgent.objects.create(workspace=workspace, name="PM", provider="openai", model="gpt", api_key="encrypted")
        ProjectAIAgent.objects.create(project=project, agent=pm, role=AgentRole.PROJECT_MANAGER)

        response = session_client.post(self.url(workspace, project, issue), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "developer" in response.data["error"]
        assert "QA" in response.data["error"]

    @pytest.mark.django_db
    def test_only_one_run_can_be_active(self, session_client, workspace, project, positions, issue):
        AgentRun.objects.create(project=project, workspace=workspace, issue=issue, status=AgentRunStatus.RUNNING)

        response = session_client.post(self.url(workspace, project, issue), format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.django_db
    @patch("plane.app.views.agent_run.run_agent_pipeline.delay")
    def test_retry_restarts_a_failed_run(self, mock_delay, session_client, workspace, project, positions, issue):
        run = AgentRun.objects.create(
            project=project,
            workspace=workspace,
            issue=issue,
            status=AgentRunStatus.FAILED,
            stage=AgentRunStage.QA,
            error="boom",
        )

        response = session_client.post(
            f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/{run.id}/retry/",
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        run.refresh_from_db()
        assert run.status == AgentRunStatus.QUEUED
        assert run.stage == AgentRunStage.PM
        assert run.error == ""
        mock_delay.assert_called_once_with(str(run.id))

    def team_url(self, workspace, project):
        return f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-team/"

    @pytest.mark.django_db
    def test_team_size_defaults(self, session_client, workspace, project):
        response = session_client.get(self.team_url(workspace, project))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["pm_count"] == 1
        assert response.data["qa_count"] == 1
        assert response.data["developer_count"] == 3

    @pytest.mark.django_db
    def test_team_size_rejects_out_of_range(self, session_client, workspace, project):
        response = session_client.patch(self.team_url(workspace, project), {"developer_count": 11}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.django_db
    @patch("plane.app.views.agent_run.run_agent_pipeline.delay")
    def test_team_size_caps_the_agents_used(self, mock_delay, session_client, workspace, project, positions, issue):
        second_dev = AIAgent.objects.create(
            workspace=workspace, name="Dev 2", provider="openai", model="gpt-test", api_key="encrypted"
        )
        second_qa = AIAgent.objects.create(
            workspace=workspace, name="QA 2", provider="openai", model="gpt-test", api_key="encrypted"
        )
        ProjectAIAgent.objects.create(project=project, agent=second_dev, role=AgentRole.DEVELOPER)
        ProjectAIAgent.objects.create(project=project, agent=second_qa, role=AgentRole.QA)
        session_client.patch(self.team_url(workspace, project), {"qa_count": 2, "developer_count": 1}, format="json")

        response = session_client.post(self.url(workspace, project, issue), format="json")

        assert response.status_code == status.HTTP_201_CREATED
        run = AgentRun.objects.get(issue=issue)
        assert len(run.developer_agents) == 1
        assert len(run.qa_agents) == 2

    @pytest.mark.django_db
    def test_retry_keeps_existing_subtasks(self, session_client, workspace, project, positions, issue):
        run = AgentRun.objects.create(
            project=project,
            workspace=workspace,
            issue=issue,
            status=AgentRunStatus.FAILED,
            stage=AgentRunStage.DEVELOPER,
        )
        child = Issue.objects.create(name="Subtask", project=project, workspace=workspace, parent=issue)
        AgentRunTask.objects.create(run=run, issue=child, agent=positions[AgentRole.DEVELOPER])

        with patch("plane.app.views.agent_run.run_agent_pipeline.delay"):
            response = session_client.post(
                f"/api/workspaces/{workspace.slug}/projects/{project.id}/agent-runs/{run.id}/retry/",
                format="json",
            )

        assert response.status_code == status.HTTP_200_OK
        run.refresh_from_db()
        assert run.stage == AgentRunStage.DEVELOPER
