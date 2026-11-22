# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    BackstageConnectionEndpoint,
    BackstageTestEndpoint,
    GitHubConnectionEndpoint,
    GitHubRepositoriesEndpoint,
    ProjectIntegrationEndpoint,
    ProjectSlackTestEndpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/integrations/github/",
        GitHubConnectionEndpoint.as_view(),
        name="github-integration",
    ),
    path(
        "workspaces/<str:slug>/integrations/github/repositories/",
        GitHubRepositoriesEndpoint.as_view(),
        name="github-repositories",
    ),
    path(
        "workspaces/<str:slug>/integrations/backstage/",
        BackstageConnectionEndpoint.as_view(),
        name="backstage-integration",
    ),
    path(
        "workspaces/<str:slug>/integrations/backstage/test/",
        BackstageTestEndpoint.as_view(),
        name="backstage-integration",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/integrations/",
        ProjectIntegrationEndpoint.as_view(),
        name="project-integrations",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/integrations/slack-test/",
        ProjectSlackTestEndpoint.as_view(),
        name="project-integrations",
    ),
]
