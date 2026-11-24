# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models
from django.db.models import Q

# Module imports
from .base import BaseModel
from .project import ProjectBaseModel


class AgentRunStatus(models.TextChoices):
    QUEUED = "queued", "Queued"
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class AgentRunStage(models.TextChoices):
    PM = "pm", "Project manager"
    DEVELOPER = "developer", "Developer"
    QA = "qa", "QA"
    FINAL = "final", "Final"
    DONE = "done", "Done"


class AgentRun(ProjectBaseModel):
    """One end-to-end agent attempt on a work item: plan, build, review, final."""

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="agent_runs")
    status = models.CharField(max_length=20, choices=AgentRunStatus.choices, default=AgentRunStatus.QUEUED)
    stage = models.CharField(max_length=20, choices=AgentRunStage.choices, default=AgentRunStage.PM)
    pm_agent = models.ForeignKey("db.AIAgent", on_delete=models.SET_NULL, null=True, related_name="pm_runs")
    # Snapshots of the team at start, so history survives assignment changes
    pm_agents = models.JSONField(default=list)
    developer_agents = models.JSONField(default=list)
    designer_agents = models.JSONField(default=list)
    qa_agent = models.ForeignKey("db.AIAgent", on_delete=models.SET_NULL, null=True, related_name="qa_runs")
    qa_agents = models.JSONField(default=list)
    # Model resolved per agent at start (position model_override when set), {agent_id: model}
    agent_models = models.JSONField(default=dict)
    # Extra developer+QA rounds run after a QA rejection
    rework_round = models.PositiveSmallIntegerField(default=0)
    result = models.TextField(blank=True)
    error = models.TextField(blank=True)
    parent_run = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="follow_ups")
    instructions = models.TextField(blank=True)
    trigger_comment = models.ForeignKey(
        "db.IssueComment", on_delete=models.SET_NULL, null=True, blank=True, related_name="agent_runs"
    )
    repository = models.CharField(max_length=500, blank=True)
    base_branch = models.CharField(max_length=255, blank=True)
    branch = models.CharField(max_length=255, blank=True)
    base_sha = models.CharField(max_length=64, blank=True)
    head_sha = models.CharField(max_length=64, blank=True)
    pull_request_number = models.PositiveIntegerField(null=True, blank=True)
    pull_request_url = models.URLField(max_length=1000, blank=True)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    def __str__(self):
        return f"{self.issue_id} <{self.status}>"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["issue"],
                condition=Q(status__in=["queued", "running"], deleted_at__isnull=True),
                name="agent_run_one_active_per_issue",
            )
        ]
        verbose_name = "Agent Run"
        verbose_name_plural = "Agent Runs"
        db_table = "agent_runs"
        ordering = ("-created_at",)


class AgentRunEvent(BaseModel):
    """Append-only history of a run: stage changes, model output and failures."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="events")
    stage = models.CharField(max_length=20, blank=True)
    kind = models.CharField(max_length=30)
    message = models.TextField(blank=True)
    data = models.JSONField(default=dict)
    agent = models.ForeignKey("db.AIAgent", on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        verbose_name = "Agent Run Event"
        verbose_name_plural = "Agent Run Events"
        db_table = "agent_run_events"
        ordering = ("created_at",)


class AgentRunArtifact(BaseModel):
    """Large outputs kept for display and for debugging a failed run."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="artifacts")
    kind = models.CharField(max_length=30)
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True)
    data = models.JSONField(default=dict)

    class Meta:
        verbose_name = "Agent Run Artifact"
        verbose_name_plural = "Agent Run Artifacts"
        db_table = "agent_run_artifacts"
        ordering = ("created_at",)


class AgentRunTask(BaseModel):
    """A child work item created by the PM stage and worked on by one developer agent."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="tasks")
    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="agent_run_tasks")
    agent = models.ForeignKey("db.AIAgent", on_delete=models.SET_NULL, null=True, related_name="run_tasks")
    role = models.CharField(max_length=20, default="developer")  # "developer" or "designer"
    status = models.CharField(max_length=20, default="pending")
    output = models.TextField(blank=True)
    paths = models.JSONField(default=list)
    depends_on = models.JSONField(default=list)
    session_id = models.CharField(max_length=140, blank=True)
    base_sha = models.CharField(max_length=64, blank=True)
    commit_sha = models.CharField(max_length=64, blank=True)
    merged_sha = models.CharField(max_length=64, blank=True)
    changed_paths = models.JSONField(default=list)
    conflict_attempts = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Agent Run Task"
        verbose_name_plural = "Agent Run Tasks"
        db_table = "agent_run_tasks"
        ordering = ("created_at",)


class AgentRunSessionStatus(models.TextChoices):
    RUNNING = "running", "Running"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"


class AgentRunSession(BaseModel):
    """One model call of a run: the exact request, the reply, its reasoning and token usage."""

    run = models.ForeignKey(AgentRun, on_delete=models.CASCADE, related_name="sessions")
    task = models.ForeignKey(AgentRunTask, on_delete=models.SET_NULL, null=True, blank=True, related_name="sessions")
    agent = models.ForeignKey(
        "db.AIAgent", on_delete=models.SET_NULL, null=True, blank=True, related_name="run_sessions"
    )
    agent_name = models.CharField(max_length=255, blank=True)
    stage = models.CharField(max_length=20, blank=True)
    purpose = models.CharField(max_length=30, blank=True)
    scope = models.CharField(max_length=140, blank=True)
    session_id = models.CharField(max_length=140, blank=True)
    rework_round = models.PositiveSmallIntegerField(default=0)
    provider = models.CharField(max_length=50, blank=True)
    model = models.CharField(max_length=255, blank=True)
    effort = models.CharField(max_length=20, blank=True)
    status = models.CharField(
        max_length=20, choices=AgentRunSessionStatus.choices, default=AgentRunSessionStatus.RUNNING
    )
    max_tokens = models.PositiveIntegerField(null=True, blank=True)
    request = models.JSONField(default=dict)
    response = models.TextField(blank=True)
    reasoning = models.TextField(blank=True)
    stop_reason = models.CharField(max_length=50, blank=True)
    usage = models.JSONField(default=dict)
    tool_calls = models.JSONField(default=list)
    cost = models.DecimalField(max_digits=14, decimal_places=6, null=True, blank=True)
    input_tokens = models.PositiveIntegerField(null=True, blank=True)
    output_tokens = models.PositiveIntegerField(null=True, blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)
    duration_ms = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "Agent Run Session"
        verbose_name_plural = "Agent Run Sessions"
        db_table = "agent_run_sessions"
        ordering = ("created_at",)
        indexes = [models.Index(fields=["run", "created_at"], name="agent_run_session_run_idx")]


class AgentSessionReset(ProjectBaseModel):
    """Each row moves one opencode session (issue + agent + scope) to a fresh session id."""

    issue = models.ForeignKey("db.Issue", on_delete=models.CASCADE, related_name="agent_session_resets")
    agent = models.ForeignKey("db.AIAgent", on_delete=models.CASCADE, related_name="session_resets")
    scope = models.CharField(max_length=140)

    class Meta:
        verbose_name = "Agent Session Reset"
        verbose_name_plural = "Agent Session Resets"
        db_table = "agent_session_resets"
        ordering = ("created_at",)
