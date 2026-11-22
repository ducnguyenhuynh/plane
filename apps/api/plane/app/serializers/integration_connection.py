# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import BackstageConnection, GitHubConnection, ProjectIntegration
from plane.license.utils.encryption import encrypt_data

from .base import BaseSerializer

SLACK_EVENT_KEYS = ("issue_created", "issue_updated", "comment_created")


class GitHubConnectionSerializer(BaseSerializer):
    token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_token = serializers.SerializerMethodField()

    class Meta:
        model = GitHubConnection
        fields = ["id", "login", "is_active", "token", "has_token", "created_at", "updated_at"]
        read_only_fields = ["id", "login", "created_at", "updated_at"]

    def get_has_token(self, obj):
        return bool(obj.token)

    def create(self, validated_data):
        token = validated_data.pop("token", "")
        if token:
            validated_data["token"] = encrypt_data(token)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # An empty token means "keep the stored one"
        token = validated_data.pop("token", "")
        if token:
            validated_data["token"] = encrypt_data(token)
        return super().update(instance, validated_data)


class BackstageConnectionSerializer(BaseSerializer):
    token = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_token = serializers.SerializerMethodField()

    class Meta:
        model = BackstageConnection
        fields = ["id", "base_url", "token", "has_token", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_has_token(self, obj):
        return bool(obj.token)

    def create(self, validated_data):
        token = validated_data.pop("token", "")
        if token:
            validated_data["token"] = encrypt_data(token)
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # An empty token means "keep the stored one"
        token = validated_data.pop("token", "")
        if token:
            validated_data["token"] = encrypt_data(token)
        return super().update(instance, validated_data)


class ProjectIntegrationSerializer(BaseSerializer):
    slack_webhook_url = serializers.CharField(write_only=True, required=False, allow_blank=True)
    has_slack_webhook = serializers.SerializerMethodField()

    class Meta:
        model = ProjectIntegration
        fields = [
            "id",
            "project_id",
            "github_repository",
            "github_repository_url",
            "github_default_branch",
            "is_github_enabled",
            "slack_webhook_url",
            "has_slack_webhook",
            "slack_events",
            "is_slack_enabled",
            "backstage_entity_ref",
            "is_backstage_enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "project_id", "github_repository_url", "created_at", "updated_at"]

    def get_has_slack_webhook(self, obj):
        return bool(obj.slack_webhook_url)

    def validate_slack_events(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Events must be an object.")
        unknown = set(value) - set(SLACK_EVENT_KEYS)
        if unknown:
            raise serializers.ValidationError(f"Unknown events: {', '.join(sorted(unknown))}.")
        events = {key: True for key in SLACK_EVENT_KEYS if key != "issue_updated"}
        events["issue_updated"] = False
        events.update({key: bool(val) for key, val in value.items()})
        return events

    def validate(self, data):
        instance = self.instance
        repository = data.get("github_repository", getattr(instance, "github_repository", ""))
        webhook = data.get("slack_webhook_url", "")
        webhook_stored = getattr(instance, "slack_webhook_url", "")
        entity_ref = data.get("backstage_entity_ref", getattr(instance, "backstage_entity_ref", ""))

        if data.get("is_github_enabled") and not repository:
            raise serializers.ValidationError({"github_repository": "Pick a repository before enabling GitHub."})
        if data.get("is_slack_enabled") and not (webhook or webhook_stored):
            raise serializers.ValidationError({"slack_webhook_url": "A Slack webhook URL is required."})
        if data.get("is_backstage_enabled") and not entity_ref:
            raise serializers.ValidationError({"backstage_entity_ref": "An entity reference is required."})

        if repository:
            data["github_repository"] = repository.strip()
            data["github_repository_url"] = f"https://github.com/{repository.strip()}"
        else:
            data["github_repository_url"] = ""
        return data

    def update(self, instance, validated_data):
        webhook = validated_data.pop("slack_webhook_url", "")
        if webhook:
            validated_data["slack_webhook_url"] = encrypt_data(webhook)
        return super().update(instance, validated_data)
