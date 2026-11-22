# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.db import models

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
    qa_agent = models.ForeignKey("db.AIAgent", on_delete=models.SET_NULL, null=True, related_name="qa_runs")
    qa_agents = models.JSONField(default=list)
    result = models.TextField(blank=True)
    error = models.TextField(blank=True)
    started_at = models.DateTimeField(null=True)
    completed_at = models.DateTimeField(null=True)

    def __str__(self):
        return f"{self.issue_id} <{self.status}>"

    class Meta:
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
    status = models.CharField(max_length=20, default="pending")
    output = models.TextField(blank=True)

    class Meta:
        verbose_name = "Agent Run Task"
        verbose_name_plural = "Agent Run Tasks"
        db_table = "agent_run_tasks"
        ordering = ("created_at",)
