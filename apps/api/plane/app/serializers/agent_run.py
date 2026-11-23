# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import AIAgent, AgentRun, AgentRunArtifact, AgentRunEvent, AgentRunSession, AgentRunTask

from .base import BaseSerializer


class AgentRunAgentSerializer(BaseSerializer):
    class Meta:
        model = AIAgent
        fields = ["id", "name", "provider", "model"]


class AgentRunTeamAgentSerializer(BaseSerializer):
    bot_user_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = AIAgent
        fields = ["id", "name", "provider", "model", "bot_user_id"]


def team_agents(run):
    """The run's agents per role, in snapshot order; old runs fall back to the single agents."""
    ids = list(run.pm_agents or []) + list(run.developer_agents or []) + list(run.qa_agents or [])
    agents = {str(agent.id): agent for agent in AIAgent.objects.filter(id__in=ids)}

    def pick(snapshot, fallback):
        roles = [agents.get(str(agent_id)) for agent_id in snapshot or []]
        roles = [agent for agent in roles if agent]
        return roles or ([fallback] if fallback else [])

    return {
        "pm": pick(run.pm_agents, run.pm_agent),
        "developer": pick(run.developer_agents, None),
        "qa": pick(run.qa_agents, run.qa_agent),
    }


class AgentRunEventSerializer(BaseSerializer):
    agent = AgentRunAgentSerializer(read_only=True)

    class Meta:
        model = AgentRunEvent
        fields = ["id", "stage", "kind", "message", "data", "agent", "created_at"]


class AgentRunArtifactSerializer(BaseSerializer):
    class Meta:
        model = AgentRunArtifact
        fields = ["id", "kind", "title", "content", "data", "created_at"]


class AgentRunTaskSerializer(BaseSerializer):
    issue_identifier = serializers.SerializerMethodField()
    issue_name = serializers.SerializerMethodField()
    agent = AgentRunAgentSerializer(read_only=True)

    class Meta:
        model = AgentRunTask
        fields = ["id", "issue_id", "issue_identifier", "issue_name", "agent", "status", "output", "created_at"]

    def get_issue_identifier(self, obj):
        return f"{obj.issue.project.identifier}-{obj.issue.sequence_id}"

    def get_issue_name(self, obj):
        return obj.issue.name


class AgentRunSerializer(BaseSerializer):
    pm_agent = AgentRunAgentSerializer(read_only=True)
    qa_agent = AgentRunAgentSerializer(read_only=True)
    team = serializers.SerializerMethodField()

    class Meta:
        model = AgentRun
        fields = [
            "id",
            "issue_id",
            "status",
            "stage",
            "pm_agent",
            "qa_agent",
            "team",
            "rework_round",
            "result",
            "error",
            "created_at",
            "started_at",
            "completed_at",
        ]

    def get_team(self, obj):
        return {role: AgentRunTeamAgentSerializer(agents, many=True).data for role, agents in team_agents(obj).items()}


class AgentRunDetailSerializer(AgentRunSerializer):
    events = AgentRunEventSerializer(many=True, read_only=True)
    artifacts = AgentRunArtifactSerializer(many=True, read_only=True)
    tasks = AgentRunTaskSerializer(many=True, read_only=True)

    class Meta(AgentRunSerializer.Meta):
        fields = AgentRunSerializer.Meta.fields + ["events", "artifacts", "tasks"]


class AgentRunSessionSerializer(BaseSerializer):
    class Meta:
        model = AgentRunSession
        fields = [
            "id",
            "run_id",
            "task_id",
            "agent_id",
            "agent_name",
            "stage",
            "purpose",
            "rework_round",
            "provider",
            "model",
            "effort",
            "status",
            "stop_reason",
            "max_tokens",
            "input_tokens",
            "output_tokens",
            "error",
            "started_at",
            "completed_at",
            "duration_ms",
            "created_at",
        ]
        read_only_fields = fields


class AgentRunSessionDetailSerializer(AgentRunSessionSerializer):
    class Meta(AgentRunSessionSerializer.Meta):
        fields = AgentRunSessionSerializer.Meta.fields + ["request", "response", "reasoning", "usage"]
        read_only_fields = fields
