# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.urls import path

from plane.app.views import (
    AgentRunCancelEndpoint,
    AgentRunDetailEndpoint,
    AgentRunRetryEndpoint,
    AgentRunSessionDetailEndpoint,
    AgentRunSessionListEndpoint,
    AgentRunSessionResetEndpoint,
    IssueAgentRunEndpoint,
    ProjectAgentRunListEndpoint,
    ProjectAgentTeamEndpoint,
)

urlpatterns = [
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-team/",
        ProjectAgentTeamEndpoint.as_view(),
        name="project-agent-team",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/issues/<uuid:issue_id>/agent-runs/",
        IssueAgentRunEndpoint.as_view(),
        name="agent-runs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/",
        ProjectAgentRunListEndpoint.as_view(),
        name="agent-runs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/<uuid:run_id>/",
        AgentRunDetailEndpoint.as_view(),
        name="agent-runs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/<uuid:run_id>/sessions/",
        AgentRunSessionListEndpoint.as_view(),
        name="agent-run-sessions",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/<uuid:run_id>/sessions/<uuid:session_id>/",
        AgentRunSessionDetailEndpoint.as_view(),
        name="agent-run-sessions",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/<uuid:run_id>/sessions/<uuid:session_id>/reset/",
        AgentRunSessionResetEndpoint.as_view(),
        name="agent-run-sessions",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/<uuid:run_id>/retry/",
        AgentRunRetryEndpoint.as_view(),
        name="agent-runs",
    ),
    path(
        "workspaces/<str:slug>/projects/<uuid:project_id>/agent-runs/<uuid:run_id>/cancel/",
        AgentRunCancelEndpoint.as_view(),
        name="agent-runs",
    ),
]
