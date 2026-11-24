# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel
from .project import ProjectBaseModel


class AgentProvider(models.TextChoices):
    # Old providers stay valid so existing rows keep loading; only OpenCode may be created
    ANTHROPIC = "anthropic", "Anthropic"
    OPENAI = "openai", "OpenAI"
    OPENCODE = "opencode", "OpenCode"
    CUSTOM = "custom", "Custom (OpenAI-compatible)"


OPENCODE_BASE_URL = "https://opencode.ai/inference/go/v1"


class AgentRole(models.TextChoices):
    PROJECT_MANAGER = "project_manager", "Project Manager"
    REVIEWER = "reviewer", "Reviewer"
    QA = "qa", "QA"
    DEVELOPER = "developer", "Developer"
    DESIGNER = "designer", "Designer"


class ReasoningEffort(models.TextChoices):
    LOW = "low", "Low"
    MEDIUM = "medium", "Medium"
    HIGH = "high", "High"
    XHIGH = "xhigh", "Extra high"
    MAX = "max", "Max"


# Single source of defaults for a project's agent team, shared by the model,
# the serializer and the view.
DEFAULT_AGENT_TEAM = {"pm_count": 1, "qa_count": 1, "developer_count": 3, "designer_count": 1}
MAX_TEAM_COUNT = 10

# Registries the sandboxes may reach when installing dependencies
DEFAULT_AGENT_ALLOWED_HOSTS = [
    "registry.npmjs.org",
    "pypi.org",
    "files.pythonhosted.org",
    "proxy.golang.org",
    "sum.golang.org",
    "index.crates.io",
    "static.crates.io",
    "crates.io",
]
AGENT_WEBSEARCH_HOST = "mcp.exa.ai"


class AIAgent(BaseModel):
    """A workspace level AI agent: which provider/model it talks to and how."""

    workspace = models.ForeignKey("db.Workspace", on_delete=models.CASCADE, related_name="ai_agents")
    name = models.CharField(max_length=255)
    provider = models.CharField(max_length=30, choices=AgentProvider.choices, default=AgentProvider.OPENCODE)
    model = models.CharField(max_length=255)
    # Encrypted with plane.license.utils.encryption before being stored, never serialized back
    api_key = models.TextField(blank=True)
    base_url = models.URLField(max_length=1000, blank=True)
    system_prompt = models.TextField(blank=True)
    # Empty means "use the provider/model default"
    reasoning_effort = models.CharField(max_length=20, blank=True, choices=ReasoningEffort.choices, default="")
    is_active = models.BooleanField(default=True)
    # The Plane member this agent acts as; see plane.utils.agent_bot.ensure_agent_bot
    bot_user = models.ForeignKey(
        "db.User",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="ai_agent_profiles",
    )

    def __str__(self):
        return f"{self.name} <{self.model}>"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "name"],
                condition=Q(deleted_at__isnull=True),
                name="ai_agent_unique_workspace_name_when_deleted_at_null",
            )
        ]
        verbose_name = "AI Agent"
        verbose_name_plural = "AI Agents"
        db_table = "ai_agents"
        ordering = ("name",)


class ProjectAIAgent(ProjectBaseModel):
    """Places a workspace agent in a role (position) on a project."""

    agent = models.ForeignKey("db.AIAgent", on_delete=models.CASCADE, related_name="project_assignments")
    role = models.CharField(max_length=30, choices=AgentRole.choices)
    model_override = models.CharField(max_length=255, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.project.name} - {self.role}: {self.agent.name}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project", "agent", "role"],
                condition=Q(deleted_at__isnull=True),
                name="project_ai_agent_unique_position_when_deleted_at_null",
            )
        ]
        verbose_name = "Project AI Agent"
        verbose_name_plural = "Project AI Agents"
        db_table = "project_ai_agents"
        ordering = ("role", "created_at")


class ProjectAgentTeam(ProjectBaseModel):
    """How many agents of each role can work in parallel on this project."""

    pm_count = models.PositiveSmallIntegerField(default=DEFAULT_AGENT_TEAM["pm_count"])
    qa_count = models.PositiveSmallIntegerField(default=DEFAULT_AGENT_TEAM["qa_count"])
    developer_count = models.PositiveSmallIntegerField(default=DEFAULT_AGENT_TEAM["developer_count"])
    designer_count = models.PositiveSmallIntegerField(default=DEFAULT_AGENT_TEAM["designer_count"])
    allowed_hosts = models.JSONField(default=list)
    websearch_enabled = models.BooleanField(default=False)
    webfetch_enabled = models.BooleanField(default=False)
    skills = models.JSONField(default=list)
    mcp_servers = models.JSONField(default=list)

    def __str__(self):
        return f"{self.project.name} team"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["project"],
                condition=Q(deleted_at__isnull=True),
                name="project_agent_team_unique_project_when_deleted_at_null",
            )
        ]
        verbose_name = "Project Agent Team"
        verbose_name_plural = "Project Agent Teams"
        db_table = "project_agent_teams"
