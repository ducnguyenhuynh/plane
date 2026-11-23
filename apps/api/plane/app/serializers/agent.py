# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import MAX_TEAM_COUNT, AIAgent, AgentProvider, AgentRole, ProjectAgentTeam, ProjectAIAgent
from plane.license.utils.encryption import encrypt_data

from .base import BaseSerializer


class AIAgentSerializer(BaseSerializer):
    api_key = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_api_key = serializers.SerializerMethodField()
    bot_user_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = AIAgent
        fields = [
            "id",
            "workspace_id",
            "name",
            "provider",
            "model",
            "base_url",
            "system_prompt",
            "reasoning_effort",
            "is_active",
            "api_key",
            "has_api_key",
            "bot_user_id",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["workspace_id", "created_at", "updated_at"]

    def get_has_api_key(self, obj):
        return bool(obj.api_key)

    def validate(self, data):
        provider = data.get("provider", getattr(self.instance, "provider", None))
        base_url = data.get("base_url", getattr(self.instance, "base_url", None))
        api_key = data.get("api_key", getattr(self.instance, "api_key", None))

        if provider in (AgentProvider.OPENCODE, AgentProvider.CUSTOM) and not base_url:
            raise serializers.ValidationError({"base_url": "Base URL is required for this provider."})
        if not api_key:
            raise serializers.ValidationError({"api_key": "API key is required."})
        return data

    def create(self, validated_data):
        # shortcut: keys are encrypted with SECRET_KEY, so rotating it invalidates stored keys; add a re-entry flow then
        validated_data["api_key"] = encrypt_data(validated_data.get("api_key", ""))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # An empty api_key on update means "keep the stored key", not "clear it"
        api_key = validated_data.pop("api_key", None)
        if api_key:
            validated_data["api_key"] = encrypt_data(api_key)
        return super().update(instance, validated_data)


class AIAgentLiteSerializer(BaseSerializer):
    """What a non-admin member may see: no prompt, base URL or key material."""

    bot_user_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = AIAgent
        fields = ["id", "name", "provider", "model", "is_active", "bot_user_id"]


class ProjectAIAgentSerializer(BaseSerializer):
    agent = serializers.SerializerMethodField()
    agent_id = serializers.PrimaryKeyRelatedField(queryset=AIAgent.objects.all(), source="agent", write_only=True)
    in_team = serializers.SerializerMethodField()

    class Meta:
        model = ProjectAIAgent
        fields = [
            "id",
            "project_id",
            "workspace_id",
            "agent",
            "agent_id",
            "role",
            "model_override",
            "is_active",
            "in_team",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["project_id", "workspace_id", "created_at", "updated_at"]

    def get_agent(self, obj):
        serializer = AIAgentSerializer if self.context.get("is_admin") else AIAgentLiteSerializer
        return serializer(obj.agent).data

    def get_in_team(self, obj):
        return getattr(obj, "in_team", False)

    def validate(self, data):
        project = self.context["project"]
        agent = data.get("agent", getattr(self.instance, "agent", None))
        role = data.get("role", getattr(self.instance, "role", None))

        if role == AgentRole.REVIEWER:
            raise serializers.ValidationError({"role": "The reviewer position is not used by agent runs yet."})
        if agent and agent.workspace_id != project.workspace_id:
            raise serializers.ValidationError({"agent_id": "Agent does not belong to this workspace."})

        duplicate = ProjectAIAgent.objects.filter(project=project, agent=agent, role=role)
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError({"role": "This agent already has that position on the project."})
        return data


class ProjectAgentTeamSerializer(BaseSerializer):
    class Meta:
        model = ProjectAgentTeam
        fields = ["id", "project_id", "pm_count", "qa_count", "developer_count"]
        read_only_fields = ["id", "project_id"]

    def validate_pm_count(self, value):
        return self._validate_count(value)

    def validate_qa_count(self, value):
        return self._validate_count(value)

    def validate_developer_count(self, value):
        return self._validate_count(value)

    @staticmethod
    def _validate_count(value):
        if value < 1 or value > MAX_TEAM_COUNT:
            raise serializers.ValidationError(f"Choose between 1 and {MAX_TEAM_COUNT}.")
        return value
