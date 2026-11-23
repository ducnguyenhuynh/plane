# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models
from django.db.models import Q

# Module imports
from plane.db.models.base import BaseModel
from plane.db.models.project import ProjectBaseModel


def default_slack_events():
    return {"issue_created": True, "issue_updated": False, "comment_created": True, "agent_run": True}


class GitHubConnection(BaseModel):
    """Workspace level GitHub personal access token used to browse repositories."""

    # A ForeignKey (not OneToOne) so a deleted connection can be replaced: the
    # uniqueness is enforced only over rows that are not soft-deleted.
    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="github_connection")
    # Encrypted with plane.license.utils.encryption, never serialized back
    token = models.TextField(blank=True)
    login = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.workspace.name} <{self.login}>"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                condition=Q(deleted_at__isnull=True),
                name="github_connection_unique_workspace_when_deleted_at_null",
            )
        ]
        verbose_name = "GitHub Connection"
        verbose_name_plural = "GitHub Connections"
        db_table = "github_connections"


class BackstageConnection(BaseModel):
    """Workspace level Backstage developer portal connection."""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="backstage_connection")
    base_url = models.URLField(max_length=1000, blank=True)
    # Encrypted with plane.license.utils.encryption, never serialized back
    token = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.workspace.name} <{self.base_url}>"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"],
                condition=Q(deleted_at__isnull=True),
                name="backstage_connection_unique_workspace_when_deleted_at_null",
            )
        ]
        verbose_name = "Backstage Connection"
        verbose_name_plural = "Backstage Connections"
        db_table = "backstage_connections"


class ProjectIntegration(ProjectBaseModel):
    """Per project links: GitHub repository, Slack notifications and a Backstage entity."""

    github_repository = models.CharField(max_length=500, blank=True)
    github_repository_url = models.URLField(max_length=1000, blank=True)
    github_default_branch = models.CharField(max_length=255, blank=True)
    is_github_enabled = models.BooleanField(default=False)

    # Encrypted with plane.license.utils.encryption, never serialized back
    slack_webhook_url = models.TextField(blank=True)
    slack_events = models.JSONField(default=default_slack_events)
    is_slack_enabled = models.BooleanField(default=False)

    backstage_entity_ref = models.CharField(max_length=500, blank=True)
    is_backstage_enabled = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.project.name} integrations"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project"],
                condition=Q(deleted_at__isnull=True),
                name="project_integration_unique_project_when_deleted_at_null",
            )
        ]
        verbose_name = "Project Integration"
        verbose_name_plural = "Project Integrations"
        db_table = "project_integrations"
