# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from plane.bgtasks.agent_pipeline_task import (
    AgentRuntimeError,
    TaskBlockedError,
    commit_and_integrate,
    merge_default_branch,
    write_changes_diff,
)
from plane.utils.agent_git import AgentRepository, AgentWorkspace, GitIdentity, run_git

IDENTITY = GitIdentity("Seed", "seed@example.com")
BRANCH = "plane/ENG-1"


class Record(SimpleNamespace):
    """A stand-in for a model instance that records its saves."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.saved = []

    def save(self, *args, **kwargs):
        self.saved.append(kwargs.get("update_fields"))


def _git_env(home):
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": "Seed",
        "GIT_AUTHOR_EMAIL": "seed@example.com",
        "GIT_COMMITTER_NAME": "Seed",
        "GIT_COMMITTER_EMAIL": "seed@example.com",
    }


@pytest.fixture
def state(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    seed = tmp_path / "seed"
    seed.mkdir()

    def git(*args, cwd=None, check=True):
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd or seed),
            env=_git_env(home),
            check=check,
            capture_output=True,
        )

    git("init", "--quiet", "--initial-branch=main")
    (seed / "app.py").write_text("print('v1')\n")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")

    remote = tmp_path / "remote.git"
    git("clone", "--quiet", "--bare", str(seed), str(remote), cwd=tmp_path)

    workspace = AgentWorkspace(str(tmp_path / "ws"))
    repo = AgentRepository(workspace, "acme/app", token="token", remote_url=str(remote))
    return SimpleNamespace(tmp=tmp_path, seed=seed, remote=remote, git=git, workspace=workspace, repo=repo)


def _advance_main(state, files):
    for name, content in files.items():
        path = Path(state.seed, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    state.git("add", "-A")
    state.git("commit", "-q", "-m", "advance")
    state.git("push", "--quiet", str(state.remote), "main")


def _seed_commit(state, base, key, files):
    work = state.workspace.prepare_work_dir("seed-run", "tasks", key)
    index = state.workspace.index_file("seed-run", "tasks", key)
    state.repo.checkout(base, work, index)
    for name, content in files.items():
        path = Path(work, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return state.repo.capture(work, index, [base], f"seed {key}", IDENTITY)


def _ctx(state, branch=BRANCH):
    return SimpleNamespace(
        workspace=state.workspace, repo=state.repo, branch=branch, default_branch="main", token="token"
    )


def _run():
    return Record(
        id=uuid4(),
        issue=SimpleNamespace(project=SimpleNamespace(identifier="ENG"), sequence_id=1),
        project_id=uuid4(),
        workspace=SimpleNamespace(slug="acme"),
    )


def _task(run):
    return Record(
        id=uuid4(),
        issue_id=uuid4(),
        issue=SimpleNamespace(
            project=SimpleNamespace(identifier="ENG"),
            sequence_id=2,
            name="Subtask",
            description_stripped="",
        ),
        agent=SimpleNamespace(id=uuid4(), name="Dev"),
        session_id="",
        paths=[],
        depends_on=[],
        changed_paths=[],
        commit_sha="",
        merged_sha="",
        base_sha="",
        conflict_attempts=0,
    )


def _prepare(state, run, task, base):
    work = state.workspace.prepare_work_dir(run.id, "tasks", task.id)
    index = state.workspace.index_file(run.id, "tasks", task.id)
    state.repo.checkout(base, work, index)
    return work, index


def test_commit_and_integrate_clean(state):
    state.repo.sync()
    _, base = state.repo.ensure_branch(BRANCH, "main")
    run = _run()
    task = _task(run)
    work, index = _prepare(state, run, task, base)
    Path(work, "new.txt").write_text("hello\n")

    with patch("plane.bgtasks.agent_pipeline_task.log_event"):
        commit_and_integrate(run, _ctx(state), task, work, index, base)

    assert task.base_sha == base
    assert task.commit_sha
    assert task.changed_paths == ["new.txt"]
    assert task.merged_sha == state.repo.head(BRANCH)
    message = run_git(["log", "-1", "--format=%B", task.commit_sha], git_dir=state.repo.git_dir).stdout.decode()
    assert message.splitlines()[0] == "ENG-2: Subtask"
    assert "Plane-Work-Item: ENG-2" in message
    assert f"Plane-Run: {run.id}" in message


def test_commit_and_integrate_resolves_a_conflict_with_the_agent(state):
    state.repo.sync()
    _, base = state.repo.ensure_branch(BRANCH, "main")
    first = _seed_commit(state, base, "first", {"app.py": "print('first')\n"})
    state.repo.integrate(first.sha, base, BRANCH, "first", IDENTITY)

    run = _run()
    task = _task(run)
    work, index = _prepare(state, run, task, base)
    Path(work, "app.py").write_text("print('second')\n")

    def fake_invoke(run, ctx, agent, prompt, *, purpose, scope, work_dir, read_only, task=None):
        (Path(work_dir) / "app.py").write_text("print('resolved')\n")
        return "resolved"

    with (
        patch("plane.bgtasks.agent_pipeline_task.log_event"),
        patch("plane.bgtasks.agent_pipeline_task.invoke", side_effect=fake_invoke),
    ):
        commit_and_integrate(run, _ctx(state), task, work, index, base)

    assert task.conflict_attempts == 1
    assert task.merged_sha == state.repo.head(BRANCH)
    content = run_git(["cat-file", "blob", f"{task.merged_sha}:app.py"], git_dir=state.repo.git_dir).stdout.decode()
    assert content == "print('resolved')\n"


def test_commit_and_integrate_blocks_when_conflict_markers_are_left(state):
    state.repo.sync()
    _, base = state.repo.ensure_branch(BRANCH, "main")
    first = _seed_commit(state, base, "first", {"app.py": "print('first')\n"})
    state.repo.integrate(first.sha, base, BRANCH, "first", IDENTITY)

    run = _run()
    task = _task(run)
    work, index = _prepare(state, run, task, base)
    Path(work, "app.py").write_text("print('second')\n")

    def fake_invoke(run, ctx, agent, prompt, *, purpose, scope, work_dir, read_only, task=None):
        (Path(work_dir) / "app.py").write_text("<<<<<<< HEAD\nprint('a')\n=======\nprint('b')\n>>>>>>> plane/task\n")
        return "resolved"

    with (
        patch("plane.bgtasks.agent_pipeline_task.log_event"),
        patch("plane.bgtasks.agent_pipeline_task.invoke", side_effect=fake_invoke) as invoke_mock,
    ):
        with pytest.raises(TaskBlockedError) as error:
            commit_and_integrate(run, _ctx(state), task, work, index, base)

    assert "app.py" in str(error.value)
    assert "person must resolve" in str(error.value)
    # The module constant is imported at call time, so the loop retries the resolution
    assert invoke_mock.call_count == 2
    assert task.conflict_attempts == 2


def test_commit_and_integrate_fails_a_policy_violation(state):
    state.repo.sync()
    _, base = state.repo.ensure_branch(BRANCH, "main")
    run = _run()
    task = _task(run)
    work, index = _prepare(state, run, task, base)
    Path(work, ".github", "workflows").mkdir(parents=True)
    Path(work, ".github", "workflows", "ci.yml").write_text("on: push\n")

    with patch("plane.bgtasks.agent_pipeline_task.log_event"):
        with pytest.raises(AgentRuntimeError) as error:
            commit_and_integrate(run, _ctx(state), task, work, index, base)

    assert ".github/workflows/ci.yml" in str(error.value)


def test_merge_default_branch_resolves_a_conflict(state):
    state.repo.sync()
    head, base = state.repo.ensure_branch(BRANCH, "main")
    task_commit = _seed_commit(state, base, "branch", {"app.py": "print('branch')\n"})
    run_git(["update-ref", f"refs/heads/{BRANCH}", task_commit.sha], git_dir=state.repo.git_dir)

    _advance_main(state, {"app.py": "print('main')\n"})
    state.repo.sync()
    default_sha = state.repo.resolve("refs/remotes/origin/main")

    agent = SimpleNamespace(id=uuid4(), name="Dev")
    first = Record(agent=agent, issue_id=uuid4())
    run = _run()
    run.tasks = MagicMock()
    run.tasks.select_related.return_value.filter.return_value.order_by.return_value.first.return_value = first

    def fake_invoke(run, ctx, agent, prompt, *, purpose, scope, work_dir, read_only, task=None):
        (Path(work_dir) / "app.py").write_text("print('merged')\n")
        return "resolved"

    with patch("plane.bgtasks.agent_pipeline_task.invoke", side_effect=fake_invoke):
        merge_default_branch(run, _ctx(state))

    merged = state.repo.head(BRANCH)
    assert merged != task_commit.sha
    assert head != task_commit.sha
    parents = (
        run_git(["rev-list", "--parents", "-n", "1", merged], git_dir=state.repo.git_dir).stdout.decode().split()[1:]
    )
    assert parents == [task_commit.sha, default_sha]
    assert run.head_sha == merged
    assert run.base_sha == default_sha
    content = run_git(["cat-file", "blob", f"{merged}:app.py"], git_dir=state.repo.git_dir).stdout.decode()
    assert content == "print('merged')\n"


def test_write_changes_diff_replaces_a_plane_symlink_without_writing_outside(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (work / ".plane").symlink_to(outside, target_is_directory=True)

    write_changes_diff(str(work), "diff --git a b\n")

    assert not (work / ".plane").is_symlink()
    assert (work / ".plane" / "changes.diff").read_text() == "diff --git a b\n"
    assert list(outside.iterdir()) == []


def test_write_changes_diff_replaces_a_changes_diff_symlink(tmp_path):
    work = tmp_path / "work"
    plane = work / ".plane"
    plane.mkdir(parents=True)
    target = tmp_path / "outside.txt"
    target.write_text("keep me\n")
    (plane / "changes.diff").symlink_to(target)

    write_changes_diff(str(work), "new\n")

    assert target.read_text() == "keep me\n"
    assert (plane / "changes.diff").read_text() == "new\n"
    assert not (plane / "changes.diff").is_symlink()


def test_write_changes_diff_replaces_a_regular_plane_file(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    (work / ".plane").write_text("not a directory\n")

    write_changes_diff(str(work), "new\n")

    assert (work / ".plane" / "changes.diff").read_text() == "new\n"
