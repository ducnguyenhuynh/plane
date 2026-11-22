# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import AIAgentEndpoint, AIAgentTestEndpoint, ProjectAIAgentEndpoint

urlpatterns = [
    path("workspaces/<str:slug>/ai-agents/", AIAgentEndpoint.as_view(), name="ai-agents"),
    path("workspaces/<str:slug>/ai-agents/<uuid:pk>/", AIAgentEndpoint.as_view(), name="ai-agents"),
    path("workspaces/<str:slug>/ai-agents/<uuid:pk>/test/", AIAgentTestEndpoint.as_view(), name="ai-agents"),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/ai-agents/",
        ProjectAIAgentEndpoint.as_view(),
        name="project-ai-agents",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/ai-agents/<uuid:pk>/",
        ProjectAIAgentEndpoint.as_view(),
        name="project-ai-agents",
    ),
]
