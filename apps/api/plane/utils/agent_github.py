# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Minimal GitHub REST client for agent pull requests.

The token is only ever sent in the ``Authorization`` header and is scrubbed
from every error message.
"""

# Third party imports
import requests

# Module imports
from plane.utils.agent_git import BRANCH_PREFIX, AgentGitError, validate_repository

API_ROOT = "https://api.github.com"
TIMEOUT = 20
USER_AGENT = "plane-agent"


class AgentGitHubError(Exception):
    pass


def _headers(token):
    return {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": USER_AGENT,
        "Authorization": f"Bearer {token}",
    }


def _scrub(text, token):
    if not text or not token:
        return text
    return text.replace(token, "***")


def _call(method, path, token, **kwargs):
    try:
        return requests.request(
            method,
            f"{API_ROOT}{path}",
            headers=_headers(token),
            timeout=TIMEOUT,
            **kwargs,
        )
    except requests.RequestException as error:
        raise AgentGitHubError(_scrub(f"github request failed: {error}", token)) from error


def _error_message(response, token):
    text = _scrub(response.text or "", token)
    return f"github returned {response.status_code}: {text[:500]}"


def _pull_request_data(data):
    return {
        "number": data["number"],
        "url": data.get("html_url") or data.get("url") or "",
    }


def _find_open_pull_request(token, repository, head, base):
    owner = repository.split("/", 1)[0]
    response = _call(
        "GET",
        f"/repos/{repository}/pulls",
        token,
        params={"state": "open", "head": f"{owner}:{head}", "base": base},
    )
    if response.status_code != 200:
        raise AgentGitHubError(_error_message(response, token))
    items = response.json() or []
    return items[0] if items else None


def ensure_pull_request(token, repository, head, base, title, body):
    try:
        validate_repository(repository)
    except AgentGitError as error:
        raise AgentGitHubError(str(error)) from error
    if not isinstance(head, str) or not head.startswith(BRANCH_PREFIX):
        raise AgentGitHubError(f"pull request head must start with {BRANCH_PREFIX}")
    if head == base:
        raise AgentGitHubError("pull request head and base must differ")

    existing = _find_open_pull_request(token, repository, head, base)
    if existing is not None:
        # Never overwrite a description a person may have edited
        return {**_pull_request_data(existing), "created": False}

    response = _call(
        "POST",
        f"/repos/{repository}/pulls",
        token,
        json={
            "title": title,
            "head": head,
            "base": base,
            "body": body,
            "draft": False,
            "maintainer_can_modify": True,
        },
    )
    if response.status_code == 422 and "already exists" in (response.text or "").lower():
        raced = _find_open_pull_request(token, repository, head, base)
        if raced is not None:
            return {**_pull_request_data(raced), "created": False}
    if response.status_code not in (200, 201):
        raise AgentGitHubError(_error_message(response, token))
    return {**_pull_request_data(response.json()), "created": True}
