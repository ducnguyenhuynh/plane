# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json

import pytest
import requests

from plane.utils import agent_github
from plane.utils.agent_github import AgentGitHubError, ensure_pull_request

TOKEN = "ghp_agent_secret_token"
REPOSITORY = "acme/app"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else json.dumps(payload)

    def json(self):
        return self._payload


def _mock(handler, monkeypatch):
    calls = []

    def fake_request(method, url, headers=None, timeout=None, **kwargs):
        calls.append({"method": method, "url": url, "headers": headers, "timeout": timeout, **kwargs})
        return handler(method, url, kwargs)

    monkeypatch.setattr(agent_github.requests, "request", fake_request)
    return calls


def test_ensure_returns_an_existing_pull_request_without_changing_it(monkeypatch):
    def handler(method, url, kwargs):
        if method == "GET":
            return FakeResponse(200, [{"number": 7, "html_url": "https://github.com/acme/app/pull/7"}])
        return FakeResponse(200, {"number": 7, "html_url": "https://github.com/acme/app/pull/7"})

    calls = _mock(handler, monkeypatch)
    result = ensure_pull_request(TOKEN, REPOSITORY, "plane/ENG-1", "main", "Title", "New body")

    assert result == {"number": 7, "url": "https://github.com/acme/app/pull/7", "created": False}
    # The description a person may have edited is never overwritten
    assert [call["method"] for call in calls] == ["GET"]
    get_call = calls[0]
    assert get_call["url"] == "https://api.github.com/repos/acme/app/pulls"
    assert get_call["params"] == {"state": "open", "head": "acme:plane/ENG-1", "base": "main"}
    assert get_call["headers"]["Accept"] == "application/vnd.github+json"
    assert get_call["headers"]["X-GitHub-Api-Version"] == "2022-11-28"
    assert get_call["headers"]["User-Agent"] == "plane-agent"
    assert get_call["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert get_call["timeout"] == 20


def test_ensure_creates_pull_request(monkeypatch):
    def handler(method, url, kwargs):
        if method == "GET":
            return FakeResponse(200, [])
        return FakeResponse(
            201,
            {"number": 9, "html_url": "https://github.com/acme/app/pull/9", "url": "https://api.github.com/x"},
        )

    calls = _mock(handler, monkeypatch)
    result = ensure_pull_request(TOKEN, REPOSITORY, "plane/ENG-2", "main", "Title", "Body")

    assert result == {"number": 9, "url": "https://github.com/acme/app/pull/9", "created": True}
    post = calls[-1]
    assert post["method"] == "POST"
    assert post["url"] == "https://api.github.com/repos/acme/app/pulls"
    assert post["json"] == {
        "title": "Title",
        "head": "plane/ENG-2",
        "base": "main",
        "body": "Body",
        "draft": False,
        "maintainer_can_modify": True,
    }


def test_ensure_handles_422_race(monkeypatch):
    gets = []

    def handler(method, url, kwargs):
        if method == "GET":
            gets.append(url)
            if len(gets) == 1:
                return FakeResponse(200, [])
            return FakeResponse(200, [{"number": 11, "html_url": "https://github.com/acme/app/pull/11"}])
        return FakeResponse(
            422,
            {"message": "Validation Failed"},
            text=(
                '{"message":"Validation Failed","errors":'
                '[{"message":"A pull request already exists for acme:plane/ENG-3."}]}'
            ),
        )

    calls = _mock(handler, monkeypatch)
    result = ensure_pull_request(TOKEN, REPOSITORY, "plane/ENG-3", "main", "Title", "Body")

    assert result == {"number": 11, "url": "https://github.com/acme/app/pull/11", "created": False}
    assert [call["method"] for call in calls] == ["GET", "POST", "GET"]


def test_ensure_refuses_foreign_head_and_same_base(monkeypatch):
    calls = _mock(lambda method, url, kwargs: FakeResponse(200, []), monkeypatch)

    with pytest.raises(AgentGitHubError):
        ensure_pull_request(TOKEN, REPOSITORY, "main", "release", "Title", "Body")
    with pytest.raises(AgentGitHubError):
        ensure_pull_request(TOKEN, REPOSITORY, "feature/x", "main", "Title", "Body")
    with pytest.raises(AgentGitHubError):
        ensure_pull_request(TOKEN, REPOSITORY, "plane/same", "plane/same", "Title", "Body")
    assert calls == []


def test_ensure_refuses_invalid_repository(monkeypatch):
    calls = _mock(lambda method, url, kwargs: FakeResponse(200, []), monkeypatch)

    for bad in ("acme/../evil", "../acme/app", "acme/app.git", "acme"):
        with pytest.raises(AgentGitHubError):
            ensure_pull_request(TOKEN, bad, "plane/ENG-6", "main", "Title", "Body")
    assert calls == []


def test_token_never_reaches_errors(monkeypatch):
    def handler(method, url, kwargs):
        raise requests.RequestException(f"connection failed for {TOKEN}")

    _mock(handler, monkeypatch)
    with pytest.raises(AgentGitHubError) as error:
        ensure_pull_request(TOKEN, REPOSITORY, "plane/ENG-4", "main", "Title", "Body")
    assert TOKEN not in str(error.value)
    assert "connection failed" in str(error.value)


def test_server_error_message_is_scrubbed(monkeypatch):
    _mock(
        lambda method, url, kwargs: FakeResponse(500, None, text=f"internal error token={TOKEN}"),
        monkeypatch,
    )
    with pytest.raises(AgentGitHubError) as error:
        ensure_pull_request(TOKEN, REPOSITORY, "plane/ENG-5", "main", "Title", "Body")
    assert TOKEN not in str(error.value)
    assert "500" in str(error.value)
