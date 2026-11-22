# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import AIAgent, AgentRun, AgentRunArtifact, AgentRunEvent, AgentRunTask

from .base import BaseSerializer


class AgentRunAgentSerializer(BaseSerializer):
    class Meta:
        model = AIAgent
        fields = ["id", "name", "provider", "model"]


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

    class Meta:
        model = AgentRun
        fields = [
            "id",
            "issue_id",
            "status",
            "stage",
            "pm_agent",
            "qa_agent",
            "result",
            "error",
            "created_at",
            "started_at",
            "completed_at",
        ]


class AgentRunDetailSerializer(AgentRunSerializer):
    events = AgentRunEventSerializer(many=True, read_only=True)
    artifacts = AgentRunArtifactSerializer(many=True, read_only=True)
    tasks = AgentRunTaskSerializer(many=True, read_only=True)

    class Meta(AgentRunSerializer.Meta):
        fields = AgentRunSerializer.Meta.fields + ["events", "artifacts", "tasks"]
