# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import pytest

from plane.utils.agent_schedule import ScheduledTask, normalize_scope, schedule_batches, scopes_overlap


def keys(batches):
    return [[task.key for task in batch] for batch in batches]


def test_normalize_scope_basics():
    assert normalize_scope(["./src/", " src/ ", "/src//a/"]) == ("src", "src/a")
    assert normalize_scope(["src/a/b", "src/a/b"]) == ("src/a/b",)
    assert normalize_scope(["src/"]) == ("src",)
    assert normalize_scope(["  "]) == ()
    assert normalize_scope([]) == ()
    assert normalize_scope(None) == ()


def test_normalize_scope_globs_keep_directory_prefix():
    assert normalize_scope(["src/**/*.py"]) == ("src",)
    assert normalize_scope(["src/a*.py"]) == ("src",)
    assert normalize_scope(["src/?.py"]) == ("src",)
    assert normalize_scope(["src/[ab]/x.py"]) == ("src",)
    assert normalize_scope(["src/{a,b}/x.py"]) == ("src",)
    assert normalize_scope(["*.md"]) == ()


def test_normalize_scope_rejects_parent_segments_and_whole_repo():
    assert normalize_scope(["../etc/passwd"]) == ()
    assert normalize_scope(["."]) == ()
    assert normalize_scope(["/"]) == ()
    assert normalize_scope(["src/../secrets"]) == ()
    assert normalize_scope([".github/workflows/ci.yml"]) == (".github/workflows/ci.yml",)


def test_scopes_overlap_boundaries():
    assert scopes_overlap(("src",), ("src/a",)) is True
    assert scopes_overlap(("src/a",), ("src",)) is True
    assert scopes_overlap(("src/a",), ("src/a",)) is True
    assert scopes_overlap(("src/a",), ("src/ab",)) is False
    assert scopes_overlap(("src/a",), ("src/b",)) is False
    assert scopes_overlap(("src/a", "docs"), ("docs/x",)) is True
    assert scopes_overlap((), ("src/a",)) is True
    assert scopes_overlap(("src/a",), ()) is True
    assert scopes_overlap((), ()) is True


def test_schedule_honors_dependencies():
    tasks = [
        ScheduledTask("api", ("src/api",), ("models",)),
        ScheduledTask("models", ("src/models",), ()),
        ScheduledTask("docs", ("docs",), ()),
    ]
    assert keys(schedule_batches(tasks, 4)) == [["models", "docs"], ["api"]]


def test_schedule_limits_parallelism_but_keeps_order():
    tasks = [ScheduledTask("a", ("a",)), ScheduledTask("b", ("b",)), ScheduledTask("c", ("c",))]
    assert keys(schedule_batches(tasks, 2)) == [["a", "b"], ["c"]]


def test_schedule_keeps_overlapping_tasks_in_separate_batches():
    tasks = [
        ScheduledTask("parent", ("src",)),
        ScheduledTask("child", ("src/app",)),
        ScheduledTask("other", ("docs",)),
    ]
    assert keys(schedule_batches(tasks, 3)) == [["parent", "other"], ["child"]]


def test_schedule_ignores_unknown_and_self_dependencies():
    solo = [ScheduledTask("solo", ("solo",), ("missing", "solo"))]
    assert keys(schedule_batches(solo, 2)) == [["solo"]]

    tasks = [
        ScheduledTask("a", ("a",), ("missing",)),
        ScheduledTask("b", ("b",), ("a",)),
    ]
    assert keys(schedule_batches(tasks, 2)) == [["a"], ["b"]]


def test_schedule_breaks_dependency_cycles_one_per_batch():
    tasks = [
        ScheduledTask("a", ("a",), ("b",)),
        ScheduledTask("b", ("b",), ("a",)),
        ScheduledTask("c", ("c",), ()),
    ]
    batches = schedule_batches(tasks, 4)
    assert keys(batches)[0] == ["c"]
    assert keys(batches)[1:] == [["a"], ["b"]]
    assert [task.key for batch in batches for task in batch] == ["c", "a", "b"]


def test_schedule_whole_repo_scope_never_shares_a_batch():
    tasks = [ScheduledTask("all", ()), ScheduledTask("one", ("src/a",)), ScheduledTask("two", ("src/b",))]
    assert keys(schedule_batches(tasks, 3)) == [["all"], ["one", "two"]]


def test_schedule_each_task_appears_exactly_once():
    tasks = [
        ScheduledTask("a", ("a",), ("c",)),
        ScheduledTask("b", ("b",), ()),
        ScheduledTask("c", ("c",), ("b",)),
        ScheduledTask("d", ("a",), ()),
    ]
    batches = schedule_batches(tasks, 2)
    scheduled = [task.key for batch in batches for task in batch]
    assert sorted(scheduled) == ["a", "b", "c", "d"]
    assert len(scheduled) == len(set(scheduled))
    flat = [task.key for batch in batches for task in batch]
    assert flat == ["b", "d", "c", "a"]

    with pytest.raises(ValueError):
        schedule_batches(tasks, 0)
