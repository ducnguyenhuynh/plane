# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import requests

# Django imports
from django.conf import settings
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response

# Module imports
from plane.app.permissions import ROLE, allow_permission
from plane.app.serializers.integration_connection import (
    BackstageConnectionSerializer,
    GitHubConnectionSerializer,
    ProjectIntegrationSerializer,
)
from plane.db.models import BackstageConnection, GitHubConnection, Project, ProjectIntegration, Workspace
from plane.license.utils.encryption import decrypt_data, encrypt_data
from plane.utils.url_security import pinned_fetch

from .agent import is_workspace_admin
from .base import BaseAPIView

GITHUB_API = "https://api.github.com"
GITHUB_TIMEOUT = 15
GITHUB_PAGE_SIZE = 100
GITHUB_MAX_PAGES = 10


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "plane-integration",
    }


def get_github_connection(workspace_slug: str):
    connection = GitHubConnection.objects.filter(workspace__slug=workspace_slug).first()
    if not connection:
        return None, ""
    return connection, decrypt_data(connection.token)


class GitHubConnectionEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        connection = GitHubConnection.objects.filter(workspace__slug=slug).first()
        if not connection:
            return Response({"login": "", "is_active": False, "has_token": False}, status=status.HTTP_200_OK)
        return Response(GitHubConnectionSerializer(connection).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def put(self, request, slug):
        connection, stored_token = get_github_connection(slug)
        # An empty token keeps the stored one, so the form can update other fields
        token = request.data.get("token", "") or stored_token
        if not token:
            return Response({"token": "A GitHub token is required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            response = requests.get(f"{GITHUB_API}/user", headers=github_headers(token), timeout=GITHUB_TIMEOUT)
        except requests.RequestException:
            return Response(
                {"token": "Could not reach GitHub. Check network access."}, status=status.HTTP_400_BAD_REQUEST
            )
        if response.status_code == 401:
            return Response({"token": "Invalid GitHub token."}, status=status.HTTP_400_BAD_REQUEST)
        if response.status_code != 200:
            return Response(
                {"token": f"GitHub returned HTTP {response.status_code}."}, status=status.HTTP_400_BAD_REQUEST
            )

        login = response.json().get("login", "")
        if connection:
            connection.token = encrypt_data(token)
            connection.login = login
            connection.is_active = True
            connection.save()
        else:
            connection = GitHubConnection.objects.create(
                workspace=Workspace.objects.get(slug=slug), token=encrypt_data(token), login=login
            )
        return Response(GitHubConnectionSerializer(connection).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug):
        GitHubConnection.objects.filter(workspace__slug=slug).delete()
        ProjectIntegration.objects.filter(workspace__slug=slug).update(
            github_repository="",
            github_repository_url="",
            github_default_branch="",
            is_github_enabled=False,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class GitHubRepositoriesEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def get(self, request, slug):
        _, token = get_github_connection(slug)
        if not token:
            return Response({"error": "Connect GitHub first."}, status=status.HTTP_400_BAD_REQUEST)

        query = request.GET.get("search", "").strip().lower()
        repositories = []
        # GitHub caps the repo list at 100 per page; ten pages is enough for any team
        for page in range(1, GITHUB_MAX_PAGES + 1):
            try:
                response = requests.get(
                    f"{GITHUB_API}/user/repos",
                    headers=github_headers(token),
                    params={
                        "per_page": GITHUB_PAGE_SIZE,
                        "page": page,
                        "sort": "updated",
                        "affiliation": "owner,collaborator,organization_member",
                    },
                    timeout=GITHUB_TIMEOUT,
                )
            except requests.RequestException:
                return Response({"error": "Could not reach GitHub."}, status=status.HTTP_400_BAD_REQUEST)
            if response.status_code != 200:
                return Response(
                    {"error": f"GitHub returned HTTP {response.status_code}."}, status=status.HTTP_400_BAD_REQUEST
                )

            batch = response.json() or []
            repositories.extend(
                {
                    "full_name": repo.get("full_name", ""),
                    "html_url": repo.get("html_url", ""),
                    "default_branch": repo.get("default_branch", ""),
                    "private": bool(repo.get("private")),
                }
                for repo in batch
                if not query or query in repo.get("full_name", "").lower()
            )
            if len(batch) < GITHUB_PAGE_SIZE:
                break
        return Response(repositories, status=status.HTTP_200_OK)


class BackstageConnectionEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER], level="WORKSPACE")
    def get(self, request, slug):
        connection = BackstageConnection.objects.filter(workspace__slug=slug).first()
        if not connection:
            return Response({"base_url": "", "is_active": False, "has_token": False}, status=status.HTTP_200_OK)
        return Response(BackstageConnectionSerializer(connection).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def put(self, request, slug):
        base_url = str(request.data.get("base_url", "")).strip().rstrip("/")
        if not base_url:
            return Response({"base_url": "A Backstage URL is required."}, status=status.HTTP_400_BAD_REQUEST)
        connection = BackstageConnection.objects.filter(workspace__slug=slug).first()
        serializer = BackstageConnectionSerializer(
            connection,
            data={**request.data, "base_url": base_url, "is_active": True},
            partial=bool(connection),
        )
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        if connection:
            serializer.save()
        else:
            serializer.save(workspace_id=Workspace.objects.get(slug=slug).id)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def delete(self, request, slug):
        BackstageConnection.objects.filter(workspace__slug=slug).delete()
        ProjectIntegration.objects.filter(workspace__slug=slug).update(
            backstage_entity_ref="", is_backstage_enabled=False
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class BackstageTestEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN], level="WORKSPACE")
    def post(self, request, slug):
        connection = BackstageConnection.objects.filter(workspace__slug=slug).first()
        if not connection or not connection.base_url:
            return Response({"ok": False, "message": "Configure a Backstage URL first."}, status=status.HTTP_200_OK)

        headers = {"Accept": "application/json"}
        token = decrypt_data(connection.token)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        try:
            response = pinned_fetch(
                "GET",
                f"{connection.base_url.rstrip('/')}/api/catalog/entities",
                allowed_ips=settings.WEBHOOK_ALLOWED_IPS,
                allowed_hosts=settings.WEBHOOK_ALLOWED_HOSTS,
                headers=headers,
                params={"limit": 1},
                timeout=GITHUB_TIMEOUT,
            )
        except (requests.RequestException, ValueError):
            return Response(
                {"ok": False, "message": "Could not reach Backstage. Check the URL and network access."},
                status=status.HTTP_200_OK,
            )
        if response.status_code == 200:
            return Response({"ok": True, "message": "Connection successful."}, status=status.HTTP_200_OK)
        if response.status_code in (401, 403):
            return Response(
                {"ok": False, "message": "Authentication failed. Check the Backstage token."}, status=status.HTTP_200_OK
            )
        return Response(
            {"ok": False, "message": f"Backstage returned HTTP {response.status_code}."}, status=status.HTTP_200_OK
        )


class ProjectIntegrationEndpoint(BaseAPIView):
    def _project(self, slug, project_id):
        return get_object_or_404(Project.objects.filter(workspace__slug=slug), pk=project_id)

    @allow_permission(allowed_roles=[ROLE.ADMIN, ROLE.MEMBER])
    def get(self, request, slug, project_id):
        project = self._project(slug, project_id)
        integration, _ = ProjectIntegration.objects.get_or_create(
            project=project, defaults={"workspace_id": project.workspace_id}
        )
        return Response(ProjectIntegrationSerializer(integration).data, status=status.HTTP_200_OK)

    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def patch(self, request, slug, project_id):
        project = self._project(slug, project_id)
        integration, _ = ProjectIntegration.objects.get_or_create(
            project=project, defaults={"workspace_id": project.workspace_id}
        )
        # The GitHub token is workspace-wide, so only a workspace admin may pick which repository it reaches
        if (
            "github_repository" in request.data
            and str(request.data["github_repository"] or "").strip() != integration.github_repository
            and not is_workspace_admin(request, slug)
        ):
            return Response(
                {"error": "Only workspace admins can change the GitHub repository."},
                status=status.HTTP_403_FORBIDDEN,
            )
        serializer = ProjectIntegrationSerializer(integration, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_200_OK)


class ProjectSlackTestEndpoint(BaseAPIView):
    @allow_permission(allowed_roles=[ROLE.ADMIN])
    def post(self, request, slug, project_id):
        integration = ProjectIntegration.objects.filter(project_id=project_id, project__workspace__slug=slug).first()
        webhook_url = decrypt_data(integration.slack_webhook_url) if integration else ""
        if not webhook_url:
            return Response({"ok": False, "message": "Add a Slack webhook URL first."}, status=status.HTTP_200_OK)

        project = Project.objects.get(pk=project_id)
        try:
            response = pinned_fetch(
                "POST",
                webhook_url,
                allowed_ips=settings.WEBHOOK_ALLOWED_IPS,
                allowed_hosts=settings.WEBHOOK_ALLOWED_HOSTS,
                json={"text": f"Plane test notification from {project.name}."},
                timeout=15,
            )
        except Exception:
            return Response(
                {"ok": False, "message": "Could not reach Slack. Check the webhook URL and network access."},
                status=status.HTTP_200_OK,
            )
        if response.status_code == 200:
            return Response({"ok": True, "message": "Test message sent."}, status=status.HTTP_200_OK)
        return Response(
            {"ok": False, "message": f"Slack returned HTTP {response.status_code}."}, status=status.HTTP_200_OK
        )
