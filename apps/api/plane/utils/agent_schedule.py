# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Scoping and batching for parallel agent tasks.

A task's scope is a list of repository paths; two scopes that touch the same
directory must not run at the same time, because their commits would collide
when integrated.
"""

# Python imports
from dataclasses import dataclass

_GLOB_CHARACTERS = "*?[{"


@dataclass(frozen=True)
class ScheduledTask:
    key: str
    paths: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()


def normalize_scope(paths):
    """Return a canonical scope; an empty tuple means the whole repository."""
    normalized = []
    for raw in paths or ():
        candidate = str(raw).strip()
        while candidate.startswith("./"):
            candidate = candidate[2:]
        candidate = candidate.lstrip("/")

        segments = candidate.split("/")
        if any(segment == ".." for segment in segments):
            continue

        kept = []
        for segment in segments:
            if segment in ("", "."):
                continue
            if any(character in segment for character in _GLOB_CHARACTERS):
                break
            kept.append(segment)

        if not kept:
            return ()
        path = "/".join(kept)
        if path not in normalized:
            normalized.append(path)
    return tuple(normalized)


def scopes_overlap(first, second):
    if not first or not second:
        return True
    for left in first:
        for right in second:
            if left == right:
                return True
            if left.startswith(f"{right}/") or right.startswith(f"{left}/"):
                return True
    return False


def schedule_batches(tasks, max_parallel):
    if max_parallel < 1:
        raise ValueError("max_parallel must be at least 1")

    ordered = []
    by_key = {}
    for task in tasks:
        if task.key in by_key:
            continue
        by_key[task.key] = task
        ordered.append(task.key)

    dependencies = {key: [dep for dep in by_key[key].depends_on if dep in by_key and dep != key] for key in ordered}

    scheduled = set()
    remaining = list(ordered)
    batches = []
    while remaining:
        batch = []
        deferred = []
        for key in remaining:
            if any(dep not in scheduled for dep in dependencies[key]):
                deferred.append(key)
                continue
            task = by_key[key]
            if len(batch) >= max_parallel:
                deferred.append(key)
                continue
            if any(scopes_overlap(task.paths, by_key[other].paths) for other in batch):
                deferred.append(key)
                continue
            batch.append(key)
        if not batch:
            # Dependency cycle: keep the remaining tasks, one per batch.
            batches.extend([[by_key[key]] for key in remaining])
            break
        scheduled.update(batch)
        batches.append([by_key[key] for key in batch])
        remaining = deferred
    return batches
