# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Plane membership for AI agents.

Every agent acts as a real (bot) Plane user so its work items, assignments and
comments have a visible author and fire the normal activity pipeline.
"""

# Python imports
import uuid
from urllib.parse import urlparse

# Django imports
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.utils import timezone

# Module imports
from plane.db.models import AIAgent, BotTypeEnum, ProjectMember, User, WorkspaceMember

# Members created for an agent get the plain "member" role
AGENT_MEMBER_ROLE = 15


def agent_bot_username(agent: AIAgent) -> str:
    return f"agent_{agent.id.hex}"


def agent_bot_email(agent: AIAgent) -> str:
    host = urlparse(settings.WEB_URL or "https://plane.so").hostname or "plane.so"
    return f"{agent_bot_username(agent)}@{host}"


def ensure_agent_bot(agent: AIAgent) -> User:
    """Return the agent's bot user, creating it and its workspace membership once."""
    bot = agent.bot_user
    if bot is None:
        bot, _ = User.objects.get_or_create(
            username=agent_bot_username(agent),
            defaults={
                "display_name": agent.name,
                "first_name": agent.name,
                "last_name": "",
                "is_bot": True,
                "bot_type": BotTypeEnum.AI_AGENT,
                "email": agent_bot_email(agent),
                "password": make_password(uuid.uuid4().hex),
                "is_password_autoset": True,
            },
        )
        agent.bot_user = bot
        agent.save(update_fields=["bot_user", "updated_at"])

    membership, created = WorkspaceMember.objects.get_or_create(
        workspace_id=agent.workspace_id, member=bot, defaults={"role": AGENT_MEMBER_ROLE}
    )
    if not created and not membership.is_active:
        membership.is_active = True
        membership.save(update_fields=["is_active", "updated_at"])
    return bot


def sync_agent_bot_name(agent: AIAgent) -> None:
    """Keep the bot's display name in step with the agent name."""
    bot = agent.bot_user
    if bot is None:
        return
    if bot.display_name == agent.name and bot.first_name == agent.name:
        return
    bot.display_name = agent.name
    bot.first_name = agent.name
    bot.save(update_fields=["display_name", "first_name", "updated_at"])


def deactivate_agent_bot(agent: AIAgent) -> None:
    """Deactivate every membership of a deleted agent's bot user."""
    bot = agent.bot_user
    if bot is None:
        return
    now = timezone.now()
    WorkspaceMember.objects.filter(member=bot, workspace_id=agent.workspace_id).update(is_active=False, updated_at=now)
    ProjectMember.objects.filter(member=bot, workspace_id=agent.workspace_id).update(is_active=False, updated_at=now)


def ensure_project_member(bot: User, project) -> ProjectMember:
    member, created = ProjectMember.objects.get_or_create(
        project=project, member=bot, defaults={"role": AGENT_MEMBER_ROLE}
    )
    if not created and not member.is_active:
        member.is_active = True
        member.save(update_fields=["is_active", "updated_at"])
    return member


def deactivate_project_member(bot: User, project) -> None:
    ProjectMember.objects.filter(project=project, member=bot).update(is_active=False, updated_at=timezone.now())
