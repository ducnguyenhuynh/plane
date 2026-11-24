# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from plane.app.serializers.agent import ProjectAgentTeamSerializer
from plane.db.models import DEFAULT_AGENT_ALLOWED_HOSTS, ProjectAgentTeam
from plane.license.utils.encryption import encrypt_data

SECRET = "super-secret-header-value"
MCP_URL = "https://mcp.example.com/mcp"


def _team():
    return ProjectAgentTeam(
        pm_count=1,
        qa_count=1,
        developer_count=3,
        allowed_hosts=["pypi.org"],
        mcp_servers=[
            {
                "name": "tools",
                "url": MCP_URL,
                "enabled": True,
                "headers": {"Authorization": encrypt_data(SECRET)},
            }
        ],
    )


def _mcp(headers, name="tools"):
    return {"name": name, "url": MCP_URL, "enabled": True, "headers": headers}


def test_header_values_are_never_serialized():
    data = ProjectAgentTeamSerializer(_team()).data

    assert data["mcp_servers"] == [
        {"name": "tools", "url": MCP_URL, "enabled": True, "header_names": ["Authorization"]}
    ]
    assert SECRET not in str(dict(data))
    assert data["default_allowed_hosts"] == DEFAULT_AGENT_ALLOWED_HOSTS


def test_an_empty_header_value_keeps_the_stored_secret():
    team = _team()
    stored_secret = team.mcp_servers[0]["headers"]["Authorization"]
    serializer = ProjectAgentTeamSerializer(
        team,
        data={"mcp_servers": [_mcp({"Authorization": ""})]},
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    stored = serializer.validated_data["mcp_servers"][0]["headers"]["Authorization"]
    assert stored == stored_secret


def test_an_empty_header_value_without_a_stored_secret_is_rejected():
    serializer = ProjectAgentTeamSerializer(_team(), data={"mcp_servers": [_mcp({"X-New": ""})]}, partial=True)

    assert not serializer.is_valid()
    assert "mcp_servers" in serializer.errors


def test_a_stored_secret_is_not_kept_when_the_url_changes():
    server = {**_mcp({"Authorization": ""}), "url": "https://other.example.com/mcp"}
    serializer = ProjectAgentTeamSerializer(_team(), data={"mcp_servers": [server]}, partial=True)

    assert not serializer.is_valid()
    assert "mcp_servers" in serializer.errors


def test_bad_hosts_skills_and_mcp_servers_are_rejected():
    cases = [
        {"allowed_hosts": ["not a host"]},
        {"allowed_hosts": "pypi.org"},
        {"skills": [{"name": "Bad Name", "description": "d", "content": "c"}]},
        {"skills": [{"name": "ok", "description": "two\nlines", "content": "c"}]},
        {"mcp_servers": [{"name": "tools", "url": "http://mcp.example.com/mcp"}]},
        {"mcp_servers": [{"name": "tools", "url": "https://user:pass@mcp.example.com/mcp"}]},
        {"mcp_servers": [_mcp({"Host": "evil"})]},
        {"mcp_servers": [_mcp({"Bad Header": "x"})]},
        {"mcp_servers": [_mcp({"Authorization": "a\r\nb"})]},
    ]

    for payload in cases:
        serializer = ProjectAgentTeamSerializer(data=payload, partial=True)
        assert not serializer.is_valid(), payload


def test_good_settings_are_accepted():
    serializer = ProjectAgentTeamSerializer(
        data={
            "allowed_hosts": ["PyPI.org", "pypi.org"],
            "websearch_enabled": True,
            "skills": [{"name": "review", "description": "How to review", "content": "Steps"}],
            "mcp_servers": [_mcp({"Authorization": "Bearer value"})],
        },
        partial=True,
    )

    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data["allowed_hosts"] == ["pypi.org"]
    assert serializer.validated_data["mcp_servers"][0]["headers"]["Authorization"] != "Bearer value"
