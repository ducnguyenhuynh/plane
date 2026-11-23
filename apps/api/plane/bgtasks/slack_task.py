# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings

# Third party imports
from celery import shared_task

# Module imports
from plane.db.models import Issue, IssueComment, ProjectIntegration
from plane.license.utils.encryption import decrypt_data
from plane.utils.exception_logger import log_exception
from plane.utils.url_security import pinned_fetch


def slack_message(event, verb, field, events, subject, comment_snippet=""):
    """Pick the message for an activity, or None when the project does not subscribe to it."""
    if event == "agent_run":
        # Rows saved before the key existed behave as subscribed
        if not events.get("agent_run", True):
            return None
        prefix = {
            "started": ":robot_face: Agent run started",
            "completed": ":white_check_mark: Agent run completed",
            "failed": ":x: Agent run failed",
        }.get(verb)
        if not prefix:
            return None
        suffix = f": {comment_snippet}" if comment_snippet else ""
        return f"{prefix} on {subject}{suffix}"
    if event == "issue" and verb == "created":
        return f":new: New work item: {subject}" if events.get("issue_created") else None
    if event == "issue" and verb == "updated":
        # Field-level updates are noisy; only notify when the state moves
        if field not in ("state", "state_id") or not events.get("issue_updated"):
            return None
        return f":repeat: State updated on {subject}"
    if event == "issue_comment" and verb == "created":
        if not events.get("comment_created"):
            return None
        return f":speech_balloon: New comment on {subject}: {comment_snippet}"
    return None


@shared_task
def send_slack_notification(event, verb, field, event_id, slug, origin=None):
    """Post work item activity to the project's Slack webhook when it is enabled."""
    try:
        project_id = None
        identifier = ""
        title = ""
        link = ""
        comment_snippet = ""

        if event == "issue":
            issue = Issue.objects.select_related("project").filter(pk=event_id).first()
            if not issue:
                return
            project_id = issue.project_id
            identifier = f"{issue.project.identifier}-{issue.sequence_id}"
            title = issue.name
            link = f"{origin}/{slug}/projects/{project_id}/issues/{issue.id}" if origin else ""
        elif event == "issue_comment":
            comment = IssueComment.objects.select_related("issue", "issue__project").filter(pk=event_id).first()
            if not comment or not comment.issue:
                return
            issue = comment.issue
            project_id = issue.project_id
            identifier = f"{issue.project.identifier}-{issue.sequence_id}"
            title = issue.name
            link = f"{origin}/{slug}/projects/{project_id}/issues/{issue.id}" if origin else ""
            comment_snippet = (comment.comment_stripped or "").strip()[:200]
        else:
            return

        integration = ProjectIntegration.objects.filter(project_id=project_id, is_slack_enabled=True).first()
        if not integration:
            return
        webhook_url = decrypt_data(integration.slack_webhook_url)
        if not webhook_url:
            return

        events = integration.slack_events or {}
        subject = f"<{link}|{identifier} {title}>" if link else f"{identifier} {title}"
        text = slack_message(event, verb, field, events, subject, comment_snippet)
        if not text:
            return

        pinned_fetch(
            "POST",
            webhook_url,
            allowed_ips=settings.WEBHOOK_ALLOWED_IPS,
            allowed_hosts=settings.WEBHOOK_ALLOWED_HOSTS,
            json={"text": text},
            timeout=15,
        )
    except Exception as e:
        log_exception(e)
