# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import MagicMock, patch

import pytest
from rest_framework import status

from plane.db.models import (
    BackstageConnection,
    GitHubConnection,
    Project,
    ProjectIntegration,
    ProjectMember,
    WorkspaceMember,
)
from plane.license.utils.encryption import decrypt_data, encrypt_data
from plane.tests.factories import UserFactory


@pytest.mark.contract
class TestGitHubConnection:
    @pytest.mark.django_db
    @patch("plane.app.views.integration_connection.requests.get")
    def test_connect_stores_an_encrypted_token_and_never_returns_it(self, mock_get, session_client, workspace):
        mock_get.return_value = MagicMock(status_code=200)
        mock_get.return_value.json.return_value = {"login": "octocat"}
        url = f"/api/workspaces/{workspace.slug}/integrations/github/"

        response = session_client.put(url, {"token": "ghp_secret"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["login"] == "octocat"
        assert "token" not in response.data
        connection = GitHubConnection.objects.get(workspace=workspace)
        assert connection.token != "ghp_secret"
        assert decrypt_data(connection.token) == "ghp_secret"

        assert session_client.get(url).data["login"] == "octocat"

    @pytest.mark.django_db
    @patch("plane.app.views.integration_connection.requests.get")
    def test_invalid_token_is_rejected(self, mock_get, session_client, workspace):
        mock_get.return_value = MagicMock(status_code=401)
        url = f"/api/workspaces/{workspace.slug}/integrations/github/"

        response = session_client.put(url, {"token": "bad"}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert not GitHubConnection.objects.filter(workspace=workspace).exists()

    @pytest.mark.django_db
    @patch("plane.app.views.integration_connection.requests.get")
    def test_repositories_are_filtered_by_search(self, mock_get, session_client, workspace):
        GitHubConnection.objects.create(workspace=workspace, token=encrypt_data("ghp_x"), login="octocat")
        mock_get.return_value = MagicMock(status_code=200)
        mock_get.return_value.json.return_value = [
            {
                "full_name": "octocat/hello-world",
                "html_url": "https://github.com/octocat/hello-world",
                "default_branch": "main",
            },
            {"full_name": "octocat/other", "html_url": "https://github.com/octocat/other", "default_branch": "main"},
        ]
        url = f"/api/workspaces/{workspace.slug}/integrations/github/repositories/?search=hello"

        response = session_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert [repo["full_name"] for repo in response.data] == ["octocat/hello-world"]

    @pytest.mark.django_db
    @patch("plane.app.views.integration_connection.requests.get")
    def test_repositories_page_through_the_github_list(self, mock_get, session_client, workspace):
        GitHubConnection.objects.create(workspace=workspace, token=encrypt_data("ghp_x"), login="octocat")
        first_page = [
            {"full_name": f"octocat/repo-{index}", "html_url": "", "default_branch": "main"} for index in range(100)
        ]
        first = MagicMock(status_code=200)
        first.json.return_value = first_page
        second = MagicMock(status_code=200)
        second.json.return_value = [{"full_name": "octocat/last", "html_url": "", "default_branch": "main"}]
        mock_get.side_effect = [first, second]
        url = f"/api/workspaces/{workspace.slug}/integrations/github/repositories/"

        response = session_client.get(url)

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 101
        assert mock_get.call_count == 2
        assert mock_get.call_args_list[1].kwargs["params"]["page"] == 2

    @pytest.mark.django_db
    def test_members_cannot_list_repositories(self, api_client, workspace):
        GitHubConnection.objects.create(workspace=workspace, token=encrypt_data("ghp_x"), login="octocat")
        member = UserFactory()
        WorkspaceMember.objects.create(workspace=workspace, member=member, role=15)
        api_client.force_authenticate(user=member)

        response = api_client.get(f"/api/workspaces/{workspace.slug}/integrations/github/repositories/")

        assert response.status_code == status.HTTP_403_FORBIDDEN

    @pytest.mark.django_db
    @patch("plane.app.views.integration_connection.requests.get")
    def test_github_can_be_reconnected_after_a_delete(self, mock_get, session_client, workspace):
        mock_get.return_value = MagicMock(status_code=200)
        mock_get.return_value.json.return_value = {"login": "octocat"}
        url = f"/api/workspaces/{workspace.slug}/integrations/github/"

        assert session_client.put(url, {"token": "ghp_one"}, format="json").status_code == status.HTTP_200_OK
        assert session_client.delete(url).status_code == status.HTTP_204_NO_CONTENT
        response = session_client.put(url, {"token": "ghp_two"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert GitHubConnection.objects.filter(workspace=workspace).count() == 1
        assert decrypt_data(GitHubConnection.objects.get(workspace=workspace).token) == "ghp_two"


@pytest.mark.contract
class TestBackstageConnection:
    @pytest.mark.django_db
    def test_connect_hides_the_token(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/integrations/backstage/"

        response = session_client.put(
            url, {"base_url": "https://backstage.example.com/", "token": "bs-secret"}, format="json"
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["base_url"] == "https://backstage.example.com"
        assert response.data["has_token"] is True
        assert "token" not in response.data
        connection = BackstageConnection.objects.get(workspace=workspace)
        assert decrypt_data(connection.token) == "bs-secret"

    @pytest.mark.django_db
    def test_backstage_connection_can_be_reconnected_after_a_delete(self, session_client, workspace):
        url = f"/api/workspaces/{workspace.slug}/integrations/backstage/"
        assert session_client.put(url, {"base_url": "https://one.example.com"}, format="json").status_code == 200
        assert session_client.delete(url).status_code == status.HTTP_204_NO_CONTENT

        response = session_client.put(url, {"base_url": "https://two.example.com"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert BackstageConnection.objects.filter(workspace=workspace).count() == 1
        assert BackstageConnection.objects.get(workspace=workspace).base_url == "https://two.example.com"

    @pytest.mark.django_db
    @patch("plane.app.views.integration_connection.pinned_fetch")
    def test_the_connection_test_goes_through_the_ssrf_guard(self, mock_fetch, session_client, workspace):
        BackstageConnection.objects.create(workspace=workspace, base_url="https://backstage.example.com")
        mock_fetch.return_value = MagicMock(status_code=200)

        response = session_client.post(f"/api/workspaces/{workspace.slug}/integrations/backstage/test/", format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["ok"] is True
        assert mock_fetch.call_args.args[1] == "https://backstage.example.com/api/catalog/entities"
        assert mock_fetch.call_args.kwargs["allowed_ips"] is not None
        assert mock_fetch.call_args.kwargs["allowed_hosts"] is not None


@pytest.mark.contract
class TestProjectIntegration:
    @pytest.fixture
    def project(self, workspace):
        project = Project.objects.create(name="Test Project", identifier="TP", workspace=workspace)
        ProjectMember.objects.create(project=project, workspace=workspace, member=workspace.owner, role=20)
        return project

    @pytest.mark.django_db
    def test_link_repository_derives_the_url(self, session_client, workspace, project):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/integrations/"

        response = session_client.patch(
            url,
            {
                "github_repository": "octocat/hello-world",
                "github_default_branch": "main",
                "is_github_enabled": True,
            },
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["github_repository_url"] == "https://github.com/octocat/hello-world"
        assert ProjectIntegration.objects.get(project=project).is_github_enabled is True

    @pytest.mark.django_db
    def test_slack_webhook_is_stored_encrypted_and_not_returned(self, session_client, workspace, project):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/integrations/"

        response = session_client.patch(
            url,
            {"slack_webhook_url": "https://hooks.slack.com/services/T/B/X", "is_slack_enabled": True},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["has_slack_webhook"] is True
        assert "slack_webhook_url" not in response.data
        integration = ProjectIntegration.objects.get(project=project)
        assert integration.slack_webhook_url != "https://hooks.slack.com/services/T/B/X"
        assert decrypt_data(integration.slack_webhook_url) == "https://hooks.slack.com/services/T/B/X"

    @pytest.mark.django_db
    def test_enabling_github_without_a_repository_is_rejected(self, session_client, workspace, project):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/integrations/"

        response = session_client.patch(url, {"is_github_enabled": True}, format="json")

        assert response.status_code == status.HTTP_400_BAD_REQUEST

    @pytest.mark.django_db
    def test_agent_run_notifications_are_on_by_default(self, session_client, workspace, project):
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/integrations/"

        response = session_client.patch(url, {"github_default_branch": "main"}, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["slack_events"]["agent_run"] is True
        assert response.data["slack_events"]["issue_updated"] is False

    @pytest.mark.django_db
    @patch("plane.app.views.integration_connection.pinned_fetch")
    def test_slack_test_message(self, mock_fetch, session_client, workspace, project):
        ProjectIntegration.objects.create(
            project=project,
            workspace=workspace,
            slack_webhook_url=encrypt_data("https://hooks.slack.com/services/T/B/X"),
            is_slack_enabled=True,
        )
        mock_fetch.return_value = MagicMock(status_code=200)
        url = f"/api/workspaces/{workspace.slug}/projects/{project.id}/integrations/slack-test/"

        response = session_client.post(url, format="json")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["ok"] is True
