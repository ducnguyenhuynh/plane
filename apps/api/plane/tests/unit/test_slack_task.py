# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from plane.bgtasks.slack_task import slack_message

EVENTS = {"issue_created": True, "issue_updated": False, "comment_created": True}


def test_created_work_item_is_sent_when_subscribed():
    assert slack_message("issue", "created", None, EVENTS, "DEMO-1 Fix login") == (
        ":new: New work item: DEMO-1 Fix login"
    )


def test_unsubscribed_events_are_skipped():
    events = {"issue_created": False, "issue_updated": False, "comment_created": False}
    assert slack_message("issue", "created", None, events, "DEMO-1") is None
    assert slack_message("issue_comment", "created", None, events, "DEMO-1") is None


def test_only_state_updates_are_sent_for_updates():
    subscribed = {**EVENTS, "issue_updated": True}
    assert slack_message("issue", "updated", "priority", subscribed, "DEMO-1") is None
    assert slack_message("issue", "updated", "state", subscribed, "DEMO-1") == ":repeat: State updated on DEMO-1"


def test_comment_created_includes_a_snippet():
    assert slack_message("issue_comment", "created", None, EVENTS, "DEMO-1", "Looks good") == (
        ":speech_balloon: New comment on DEMO-1: Looks good"
    )
