# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import re
from urllib.parse import urlparse, urlsplit

# Third party imports
from rest_framework import serializers

# Module imports
from plane.db.models import (
    DEFAULT_AGENT_ALLOWED_HOSTS,
    MAX_TEAM_COUNT,
    OPENCODE_BASE_URL,
    AIAgent,
    AgentProvider,
    AgentRole,
    ProjectAgentTeam,
    ProjectAIAgent,
)
from plane.license.utils.encryption import encrypt_data

from .base import BaseSerializer

# An OpenCode inference endpoint: https://opencode.ai/inference/go/<path segments>
OPENCODE_PATH_RE = re.compile(r"^/inference/go(/[A-Za-z0-9_-][A-Za-z0-9._-]*)+$")
MODEL_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")

HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
HEADER_NAME_RE = re.compile(r"^[A-Za-z0-9-]{1,64}$")
FORBIDDEN_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})
MAX_ALLOWED_HOSTS = 100
MAX_MCP_SERVERS = 20


def valid_mcp_url(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and bool(parsed.hostname)
        and HOSTNAME_RE.fullmatch(parsed.hostname) is not None
    )


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
        provider = data.get("provider") or getattr(self.instance, "provider", None) or AgentProvider.OPENCODE
        if provider != AgentProvider.OPENCODE:
            raise serializers.ValidationError({"provider": "Only the OpenCode provider is supported."})

        # An empty base_url in the request means the default, not the stored value
        if "base_url" in data:
            base_url = data["base_url"] or OPENCODE_BASE_URL
        else:
            base_url = getattr(self.instance, "base_url", None) or OPENCODE_BASE_URL
        if not self._is_opencode_base_url(base_url):
            raise serializers.ValidationError(
                {"base_url": "Use an OpenCode inference endpoint, such as https://opencode.ai/inference/go/v1."}
            )
        data["base_url"] = base_url

        model = data.get("model", getattr(self.instance, "model", "")) or ""
        if not MODEL_ID_RE.fullmatch(model):
            raise serializers.ValidationError({"model": "Enter a valid model ID."})

        api_key = data.get("api_key", getattr(self.instance, "api_key", None))
        if not api_key:
            raise serializers.ValidationError({"api_key": "API key is required."})
        return data

    @staticmethod
    def _is_opencode_base_url(base_url):
        try:
            url = urlparse(base_url)
            port = url.port
        except ValueError:
            return False
        path = url.path[:-1] if url.path.endswith("/") else url.path
        return (
            url.scheme == "https"
            and url.hostname == "opencode.ai"
            and port in (None, 443)
            and not url.username
            and not url.password
            and not url.query
            and not url.fragment
            and bool(OPENCODE_PATH_RE.fullmatch(path))
        )

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
    default_allowed_hosts = serializers.SerializerMethodField()

    class Meta:
        model = ProjectAgentTeam
        fields = [
            "id",
            "project_id",
            "pm_count",
            "qa_count",
            "developer_count",
            "allowed_hosts",
            "websearch_enabled",
            "webfetch_enabled",
            "skills",
            "mcp_servers",
            "default_allowed_hosts",
        ]
        read_only_fields = ["id", "project_id", "default_allowed_hosts"]

    def get_default_allowed_hosts(self, obj):
        return DEFAULT_AGENT_ALLOWED_HOSTS

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

    def validate_allowed_hosts(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Enter a list of hosts.")
        hosts = []
        for item in value:
            if not isinstance(item, str):
                raise serializers.ValidationError("Each host must be a string.")
            host = item.strip().lower()
            if not HOSTNAME_RE.fullmatch(host):
                raise serializers.ValidationError(f"{item} is not a valid host name.")
            if host not in hosts:
                hosts.append(host)
        if len(hosts) > MAX_ALLOWED_HOSTS:
            raise serializers.ValidationError(f"Use at most {MAX_ALLOWED_HOSTS} hosts.")
        return hosts

    def validate_skills(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Enter a list of skills.")
        skills = []
        names = set()
        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError("Each skill must be an object.")
            name = item.get("name")
            if not isinstance(name, str) or len(name) > 64 or not SLUG_RE.fullmatch(name):
                raise serializers.ValidationError({"name": "Enter a valid skill name."})
            if name in names:
                raise serializers.ValidationError({"name": "Skill names must be unique."})
            names.add(name)
            description = item.get("description")
            if (
                not isinstance(description, str)
                or not description.strip()
                or len(description) > 1024
                or "\n" in description
                or "\r" in description
            ):
                raise serializers.ValidationError({"description": "Enter one line of at most 1024 characters."})
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                raise serializers.ValidationError({"content": "Enter the skill content."})
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                raise serializers.ValidationError({"enabled": "Must be true or false."})
            skills.append({"name": name, "description": description, "content": content, "enabled": enabled})
        return skills

    def validate_mcp_servers(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Enter a list of MCP servers.")
        if len(value) > MAX_MCP_SERVERS:
            raise serializers.ValidationError(f"Use at most {MAX_MCP_SERVERS} MCP servers.")
        stored = {
            server.get("name"): server
            for server in (getattr(self.instance, "mcp_servers", None) or [])
            if isinstance(server, dict)
        }
        servers = []
        names = set()
        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError("Each MCP server must be an object.")
            name = item.get("name")
            if not isinstance(name, str) or len(name) > 40 or not SLUG_RE.fullmatch(name):
                raise serializers.ValidationError({"name": "Enter a valid MCP server name."})
            if name in names:
                raise serializers.ValidationError({"name": "MCP server names must be unique."})
            names.add(name)
            url = item.get("url")
            if not valid_mcp_url(url):
                raise serializers.ValidationError({"url": "Enter an https URL without credentials, query or fragment."})
            enabled = item.get("enabled", True)
            if not isinstance(enabled, bool):
                raise serializers.ValidationError({"enabled": "Must be true or false."})
            headers = item.get("headers") or {}
            if not isinstance(headers, dict):
                raise serializers.ValidationError({"headers": "Enter an object of headers."})
            # A saved secret only stays with the URL it was entered for, never follows it to a new one
            stored_server = stored.get(name) or {}
            stored_headers = (stored_server.get("headers") or {}) if stored_server.get("url") == url else {}
            encrypted = {}
            for key, header_value in headers.items():
                if not isinstance(key, str) or not HEADER_NAME_RE.fullmatch(key):
                    raise serializers.ValidationError({"headers": "Invalid header name."})
                if key.lower() in FORBIDDEN_HEADERS:
                    raise serializers.ValidationError({"headers": f"The {key} header cannot be set."})
                if not isinstance(header_value, str) or "\r" in header_value or "\n" in header_value:
                    raise serializers.ValidationError({"headers": f"Invalid value for {key}."})
                if header_value == "":
                    # An empty value keeps the stored secret; an omitted header is removed
                    kept = stored_headers.get(key)
                    if not kept:
                        raise serializers.ValidationError({"headers": f"Enter a value for {key}."})
                    encrypted[key] = kept
                else:
                    encrypted[key] = encrypt_data(header_value)
            servers.append({"name": name, "url": url, "enabled": enabled, "headers": encrypted})
        return servers

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Header values are secrets: only their names may leave the API
        data["mcp_servers"] = [
            {
                "name": server.get("name", ""),
                "url": server.get("url", ""),
                "enabled": bool(server.get("enabled", True)),
                "header_names": sorted(str(key) for key in (server.get("headers") or {})),
            }
            for server in (instance.mcp_servers or [])
            if isinstance(server, dict)
        ]
        return data
