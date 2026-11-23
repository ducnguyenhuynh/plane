# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Django imports
from django.conf import settings

# Third party imports
from celery import shared_task

# Module imports
from plane.db.models import AgentRun, ProjectIntegration
from plane.license.utils.encryption import decrypt_data
from plane.utils.exception_logger import log_exception
from plane.utils.url_security import pinned_fetch

from .slack_task import slack_message


def agent_run_link(run) -> str:
    origin = settings.WEB_URL or settings.APP_BASE_URL
    if not origin:
        return ""
    return f"{origin}/{run.workspace.slug}/projects/{run.project_id}/issues/{run.issue_id}"


@shared_task
def send_agent_run_slack_message(run_id, kind, message=""):
    """Post one agent run notification to the project's Slack webhook."""
    try:
        run = AgentRun.objects.select_related("issue", "issue__project", "workspace").filter(pk=run_id).first()
        if not run or not run.issue:
            return

        integration = ProjectIntegration.objects.filter(project_id=run.project_id, is_slack_enabled=True).first()
        if not integration:
            return
        webhook_url = decrypt_data(integration.slack_webhook_url)
        if not webhook_url:
            return

        identifier = f"{run.issue.project.identifier}-{run.issue.sequence_id}"
        link = agent_run_link(run)
        subject = f"<{link}|{identifier} {run.issue.name}>" if link else f"{identifier} {run.issue.name}"
        text = slack_message("agent_run", kind, None, integration.slack_events or {}, subject, message[:500])
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
