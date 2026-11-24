# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import base64
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from plane.utils.agent_git import (
    AgentGitError,
    AgentGitPolicyError,
    AgentRepository,
    AgentWorkspace,
    BranchDivergedError,
    GitIdentity,
    MergeConflictError,
    branch_for_issue,
    clear_directory,
    remove_git_entries,
    repository_url,
    run_git,
    validate_repository,
)

IDENTITY = GitIdentity("Agent", "agent@plane.so")
TOKEN = "ghs_agent_secret_token"


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
    (seed / ".gitignore").write_text("*.log\n")
    git("add", "-A")
    git("commit", "-q", "-m", "seed")

    remote = tmp_path / "remote.git"
    git("clone", "--quiet", "--bare", str(seed), str(remote), cwd=tmp_path)

    workspace = AgentWorkspace(str(tmp_path / "ws"))
    repository = AgentRepository(workspace, "acme/app", token=TOKEN, remote_url=str(remote))
    return SimpleNamespace(
        tmp=tmp_path,
        home=home,
        seed=seed,
        remote=remote,
        git=git,
        workspace=workspace,
        repo=repository,
    )


def _task_commit(state, base, key, files):
    workspace = state.workspace
    repo = state.repo
    work = workspace.prepare_work_dir("run", "tasks", key)
    index = workspace.index_file("run", "tasks", key)
    repo.checkout(base, work, index)
    for name, content in files.items():
        path = Path(work, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return repo.capture(work, index, [base], f"task {key}", IDENTITY)


def _advance_main(state, files):
    for name, content in files.items():
        path = Path(state.seed, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    state.git("add", "-A")
    state.git("commit", "-q", "-m", "advance")
    state.git("push", "--quiet", str(state.remote), "main")


def _push_branch(state, branch, files, message="branch work"):
    exists = state.git("rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False)
    if exists.returncode == 0:
        state.git("checkout", "--quiet", branch)
    else:
        state.git("checkout", "--quiet", "-b", branch, "main")
    for name, content in files.items():
        path = Path(state.seed, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    state.git("add", "-A")
    state.git("commit", "-q", "-m", message)
    state.git("push", "--quiet", str(state.remote), branch)
    state.git("checkout", "--quiet", "main")


def _rev_list_parents(repo, sha):
    out = run_git(["rev-list", "--parents", "-n", "1", sha], git_dir=repo.git_dir).stdout.decode()
    return out.split()[1:]


def test_names():
    assert repository_url("acme/app") == "https://github.com/acme/app.git"
    assert validate_repository("acme/app") == "acme/app"
    assert branch_for_issue("ENG", 12) == "plane/ENG-12"
    for bad in ("acme", "acme/app.git", "acme/../x", "../acme/x", "a b/c", "acme/.", "acme/"):
        with pytest.raises(AgentGitError):
            validate_repository(bad)
    for bad in ("ENG-1", "", "eng 1", "eng/1"):
        with pytest.raises(AgentGitError):
            branch_for_issue(bad, 1)


def test_issue_identifiers_accept_the_letters_plane_allows():
    assert branch_for_issue("ÇŞĞIİÖÜ", 7) == "plane/ÇŞĞIİÖÜ-7"


def test_clear_directory_and_remove_git_entries(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep\n")

    root = tmp_path / "root"
    (root / "nested" / "deep").mkdir(parents=True)
    (root / "nested" / "deep" / "file.txt").write_text("x\n")
    (root / "nested" / ".git" / "hooks").mkdir(parents=True)
    (root / "nested" / ".git" / "config").write_text("[core]\n")
    (root / "gitfile").write_text("gitdir: /tmp/other\n")
    (root / "gitfile").rename(root / ".git")
    os.symlink(str(outside), str(root / "linkdir"))
    (root / "nested" / "file2.txt").write_text("y\n")

    remove_git_entries(str(root))
    assert not (root / ".git").exists()
    assert not (root / "nested" / ".git").exists()
    assert (root / "nested" / "deep" / "file.txt").exists()
    assert (outside / "keep.txt").exists()

    clear_directory(str(root))
    assert list(root.iterdir()) == []
    assert (outside / "keep.txt").exists()


def test_workspace_layout_and_cleanup(state):
    workspace = state.workspace
    assert workspace.repo_dir("acme/app").endswith("repos/acme__app.git")
    assert workspace.lock_path("acme/app").endswith("locks/acme__app.lock")
    work = workspace.prepare_work_dir("run-1", "review", "task-a")
    assert work.endswith("runs/run-1/review/task-a")
    assert workspace.relative(work) == "runs/run-1/review/task-a"
    assert workspace.index_file("run-1", "review", "task-a").endswith("runs/run-1/index/review-task-a")
    assert workspace.sessions_dir("issue-1").endswith("sessions/issue-1")

    (Path(work) / "junk.txt").write_text("junk\n")
    assert workspace.prepare_work_dir("run-1", "review", "task-a") == work
    assert list(Path(work).iterdir()) == []

    sessions = workspace.prepare_sessions_dir("issue-1")
    assert (os.stat(sessions).st_mode & 0o777) == 0o700

    workspace.cleanup_run("run-1")
    assert not (state.tmp / "ws" / "runs" / "run-1").exists()

    with pytest.raises(AgentGitError):
        workspace.work_dir("../escape", "tasks", "a")
    with pytest.raises(ValueError):
        workspace.work_dir("run-1", "bogus", "a")


def test_sync_creates_bare_repository(state):
    repo = state.repo
    repo.sync()
    assert (Path(repo.git_dir) / "HEAD").exists()
    assert repo.resolve("refs/remotes/origin/main")
    repo.sync()
    assert repo.resolve("refs/remotes/origin/main")


def test_ensure_branch_new(state):
    repo = state.repo
    repo.sync()
    head, base = repo.ensure_branch("plane/ENG-1", "main")
    assert head == base == repo.resolve("refs/remotes/origin/main")
    assert repo.head("plane/ENG-1") == head


def test_ensure_branch_new_run_follows_remote(state):
    _push_branch(state, "plane/ENG-2", {"from-remote.txt": "remote\n"})
    repo = state.repo
    repo.sync()
    remote_sha = repo.resolve("refs/remotes/origin/plane/ENG-2")

    leftover = _task_commit(state, repo.resolve("refs/remotes/origin/main"), "leftover", {"local.txt": "1\n"})
    run_git(["update-ref", "refs/heads/plane/ENG-2", leftover.sha], git_dir=repo.git_dir)

    head, base = repo.ensure_branch("plane/ENG-2", "main")
    assert head == remote_sha
    assert repo.head("plane/ENG-2") == remote_sha
    assert base == repo.resolve("refs/remotes/origin/main")


def test_ensure_branch_new_run_without_remote_resets_local(state):
    repo = state.repo
    repo.sync()
    default_sha = repo.resolve("refs/remotes/origin/main")
    leftover = _task_commit(state, default_sha, "leftover", {"local.txt": "1\n"})
    run_git(["update-ref", "refs/heads/plane/ENG-2b", leftover.sha], git_dir=repo.git_dir)

    head, base = repo.ensure_branch("plane/ENG-2b", "main")
    assert head == base == default_sha
    assert repo.head("plane/ENG-2b") == default_sha


def test_ensure_branch_resume_creates_from_default(state):
    repo = state.repo
    repo.sync()
    default_sha = repo.resolve("refs/remotes/origin/main")
    head, base = repo.ensure_branch("plane/ENG-2c", "main", resume=True)
    assert head == base == default_sha
    assert repo.head("plane/ENG-2c") == default_sha


def test_ensure_branch_resume_keeps_local_when_remote_absent(state):
    repo = state.repo
    repo.sync()
    _, base = repo.ensure_branch("plane/ENG-2d", "main", resume=True)
    local = _task_commit(state, base, "resume-local", {"local.txt": "1\n"})
    run_git(["update-ref", "refs/heads/plane/ENG-2d", local.sha], git_dir=repo.git_dir)

    head, again = repo.ensure_branch("plane/ENG-2d", "main", resume=True)
    assert head == local.sha
    assert again == base


def test_ensure_branch_resume_uses_remote_when_local_absent_or_behind(state):
    _push_branch(state, "plane/ENG-2e", {"from-remote.txt": "remote\n"})
    repo = state.repo
    repo.sync()
    first_remote = repo.resolve("refs/remotes/origin/plane/ENG-2e")

    head, base = repo.ensure_branch("plane/ENG-2e", "main", resume=True)
    assert head == first_remote
    assert base == repo.resolve("refs/remotes/origin/main")

    _push_branch(state, "plane/ENG-2e", {"second.txt": "2\n"})
    repo.sync()
    second_remote = repo.resolve("refs/remotes/origin/plane/ENG-2e")
    assert second_remote != first_remote
    head_again, _ = repo.ensure_branch("plane/ENG-2e", "main", resume=True)
    assert head_again == second_remote
    assert repo.head("plane/ENG-2e") == second_remote


def test_ensure_branch_resume_keeps_local_when_ahead(state):
    _push_branch(state, "plane/ENG-2f", {"from-remote.txt": "remote\n"})
    repo = state.repo
    repo.sync()
    remote_sha = repo.resolve("refs/remotes/origin/plane/ENG-2f")
    head, base = repo.ensure_branch("plane/ENG-2f", "main", resume=True)
    assert head == remote_sha

    local = _task_commit(state, head, "local", {"local.txt": "1\n"})
    run_git(["update-ref", "refs/heads/plane/ENG-2f", local.sha], git_dir=repo.git_dir)
    head_again, base_again = repo.ensure_branch("plane/ENG-2f", "main", resume=True)
    assert head_again == local.sha
    assert base_again == base


def test_ensure_branch_resume_rejects_divergence(state):
    _push_branch(state, "plane/ENG-2g", {"from-remote.txt": "remote\n"})
    repo = state.repo
    repo.sync()
    default_sha = repo.resolve("refs/remotes/origin/main")
    local = _task_commit(state, default_sha, "divergent", {"local.txt": "1\n"})
    run_git(["update-ref", "refs/heads/plane/ENG-2g", local.sha], git_dir=repo.git_dir)

    with pytest.raises(BranchDivergedError, match="diverged from GitHub"):
        repo.ensure_branch("plane/ENG-2g", "main", resume=True)
    assert repo.head("plane/ENG-2g") == local.sha


def test_ensure_branch_missing_default(state):
    repo = state.repo
    repo.sync()
    with pytest.raises(AgentGitError):
        repo.ensure_branch("plane/ENG-1", "missing")
    with pytest.raises(AgentGitError):
        repo.ensure_branch("feature/nope", "main")


def test_checkout_has_no_git_directory(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-3", "main")
    work = state.workspace.prepare_work_dir("run", "tasks", "checkout")
    index = state.workspace.index_file("run", "tasks", "checkout")
    repo.checkout(head, work, index)
    assert Path(work, "app.py").read_text() == "print('v1')\n"
    assert not Path(work, ".git").exists()


def test_capture_commits_changes_and_honors_gitignore(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-4", "main")
    work = state.workspace.prepare_work_dir("run", "tasks", "capture")
    index = state.workspace.index_file("run", "tasks", "capture")
    repo.checkout(head, work, index)

    Path(work, "feature.txt").write_text("new feature\n")
    Path(work, "debug.log").write_text("should be ignored\n")
    Path(work, "app.py").write_text("print('v2')\n")

    result = repo.capture(work, index, [head], "add feature", IDENTITY)
    assert result.sha is not None
    assert result.paths == ["app.py", "feature.txt"]

    listing = run_git(["ls-tree", "-r", "--name-only", result.sha], git_dir=repo.git_dir).stdout.decode()
    assert "debug.log" not in listing
    assert repo.diff(head, result.sha).count("+new feature") == 1
    assert repo.changed_paths(head, result.sha) == ["app.py", "feature.txt"]


def test_capture_with_no_changes(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-5", "main")
    work = state.workspace.prepare_work_dir("run", "tasks", "empty")
    index = state.workspace.index_file("run", "tasks", "empty")
    repo.checkout(head, work, index)
    result = repo.capture(work, index, [head], "nothing", IDENTITY)
    assert result.sha is None
    assert result.paths == []


def test_planted_git_directory_and_hooks_never_execute(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-6", "main")
    work = state.workspace.prepare_work_dir("run", "tasks", "hooks")
    index = state.workspace.index_file("run", "tasks", "hooks")
    repo.checkout(head, work, index)

    sentinel = state.tmp / "hook-ran"
    hooks = Path(work, ".git", "hooks")
    hooks.mkdir(parents=True)
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n")
    hook.chmod(0o755)
    Path(work, ".git", "config").write_text("[alias]\n\tleak = !touch " + str(sentinel) + "\n")

    Path(work, "app.py").write_text("print('v3')\n")
    result = repo.capture(work, index, [head], "commit despite planted .git", IDENTITY)
    assert result.sha is not None
    assert not Path(work, ".git").exists()
    assert not sentinel.exists()

    real_hooks = Path(repo.git_dir, "hooks")
    real_hooks.mkdir(parents=True, exist_ok=True)
    pre_push = real_hooks / "pre-push"
    pre_push.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n")
    pre_push.chmod(0o755)
    run_git(["update-ref", "refs/heads/plane/ENG-6", result.sha], git_dir=repo.git_dir)
    repo.push("plane/ENG-6")
    assert not sentinel.exists()


def test_nested_git_cannot_create_gitlink(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-7", "main")
    work = state.workspace.prepare_work_dir("run", "tasks", "nested")
    index = state.workspace.index_file("run", "tasks", "nested")
    repo.checkout(head, work, index)

    nested = Path(work, "vendor", "lib")
    nested.mkdir(parents=True)
    (nested / ".git" / "objects").mkdir(parents=True)
    (nested / ".git" / "config").write_text("[core]\n\trepositoryformatversion = 1\n")
    (nested / "code.py").write_text("x = 1\n")

    result = repo.capture(work, index, [head], "nested git", IDENTITY)
    assert result.sha is not None
    listing = run_git(["ls-tree", "-r", result.sha], git_dir=repo.git_dir).stdout.decode()
    assert "160000" not in listing
    assert "vendor/lib/code.py" in listing
    assert not (nested / ".git").exists()


def test_blocked_paths_raise(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-8", "main")

    work = state.workspace.prepare_work_dir("run", "tasks", "blocked-workflow")
    index = state.workspace.index_file("run", "tasks", "blocked-workflow")
    repo.checkout(head, work, index)
    Path(work, ".github", "workflows").mkdir(parents=True)
    Path(work, ".github", "workflows", "ci.yml").write_text("on: push\n")
    with pytest.raises(AgentGitPolicyError) as error:
        repo.capture(work, index, [head], "blocked", IDENTITY)
    assert error.value.paths == [".github/workflows/ci.yml"]

    work = state.workspace.prepare_work_dir("run", "tasks", "blocked-submodules")
    index = state.workspace.index_file("run", "tasks", "blocked-submodules")
    repo.checkout(head, work, index)
    Path(work, ".gitmodules").write_text('[submodule "x"]\n\tpath = x\n')
    with pytest.raises(AgentGitPolicyError) as error:
        repo.capture(work, index, [head], "blocked", IDENTITY)
    assert error.value.paths == [".gitmodules"]


def test_escaping_symlinks_raise(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-9", "main")

    work = state.workspace.prepare_work_dir("run", "tasks", "symlink-absolute")
    index = state.workspace.index_file("run", "tasks", "symlink-absolute")
    repo.checkout(head, work, index)
    os.symlink("/etc/passwd", str(Path(work, "leak")))
    with pytest.raises(AgentGitPolicyError) as error:
        repo.capture(work, index, [head], "leak", IDENTITY)
    assert error.value.paths == ["leak"]

    work = state.workspace.prepare_work_dir("run", "tasks", "symlink-escape")
    index = state.workspace.index_file("run", "tasks", "symlink-escape")
    repo.checkout(head, work, index)
    os.symlink("../outside", str(Path(work, "up")))
    with pytest.raises(AgentGitPolicyError) as error:
        repo.capture(work, index, [head], "escape", IDENTITY)
    assert error.value.paths == ["up"]


def test_safe_relative_symlink_passes(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-11", "main")
    work = state.workspace.prepare_work_dir("run", "tasks", "symlink-safe")
    index = state.workspace.index_file("run", "tasks", "symlink-safe")
    repo.checkout(head, work, index)
    os.symlink("app.py", str(Path(work, "alias.py")))

    result = repo.capture(work, index, [head], "safe symlink", IDENTITY)
    assert result.sha is not None
    listing = run_git(["ls-tree", "-r", result.sha], git_dir=repo.git_dir).stdout.decode()
    assert "120000" in listing
    assert "alias.py" in listing


def test_symlinked_github_directory_raises(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-11b", "main")
    work = state.workspace.prepare_work_dir("run", "tasks", "github-symlink")
    index = state.workspace.index_file("run", "tasks", "github-symlink")
    repo.checkout(head, work, index)
    os.symlink("assets", str(Path(work, ".github")))

    with pytest.raises(AgentGitPolicyError) as error:
        repo.capture(work, index, [head], "symlinked .github", IDENTITY)
    assert error.value.paths == [".github"]


def test_environment_is_allowlisted_and_token_stays_out_of_argv(state, monkeypatch):
    monkeypatch.setenv("PLANE_FAKE_SECRET", "hunter2-secret")
    calls = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append({"argv": list(args[0]), "env": dict(kwargs.get("env") or {})})
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)

    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-12", "main")
    assert calls
    encoded = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    for call in calls:
        joined = " ".join(call["argv"])
        assert TOKEN not in joined
        assert encoded not in joined
        env = call["env"]
        assert "PLANE_FAKE_SECRET" not in env
        assert env["HOME"] == "/nonexistent"
        assert env["GIT_CONFIG_NOSYSTEM"] == "1"
        assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert env["LANG"] == "C.UTF-8"
        assert "os.environ" not in env
    fetch = next(call for call in calls if "fetch" in call["argv"])
    assert fetch["argv"][1:3] == ["-c", "core.hooksPath=/dev/null"]
    assert fetch["env"]["GIT_CONFIG_COUNT"] == "1"
    assert fetch["env"]["GIT_CONFIG_VALUE_0"] == f"Authorization: Basic {encoded}"

    run_git(["config", "alias.leak", "!printenv PLANE_FAKE_SECRET"], git_dir=repo.git_dir)
    alias = run_git(["leak"], git_dir=repo.git_dir, check=False)
    assert "hunter2-secret" not in (alias.stdout + alias.stderr).decode()

    with pytest.raises(AgentGitError) as error:
        run_git(["cat-file", "blob", "no-such-object"], git_dir=repo.git_dir, token=TOKEN)
    message = str(error.value)
    assert TOKEN not in message
    assert encoded not in message
    assert "no-such-object" in message


def test_installed_dependencies_are_never_committed(state):
    repo = state.repo
    repo.sync()
    head, _ = repo.ensure_branch("plane/ENG-30", "main")
    work = state.workspace.prepare_work_dir("run", "tasks", "deps")
    index = state.workspace.index_file("run", "tasks", "deps")
    repo.checkout(head, work, index)
    Path(work, "node_modules", "pkg").mkdir(parents=True)
    Path(work, "node_modules", "pkg", "index.js").write_text("module.exports = 1\n")
    Path(work, "package.json").write_text("{}\n")

    result = repo.capture(work, index, [head], "add package", IDENTITY)

    assert result.paths == ["package.json"]


def test_skills_files_have_the_expected_format(state):
    path = state.workspace.prepare_skills_dir(
        "run-skills",
        [{"name": "review", "description": 'How to "review"', "content": "Step one"}],
    )

    body = Path(path, "review", "SKILL.md").read_text()
    expected = f"---\nname: review\ndescription: {json.dumps('How to "review"')}\n---\n\nStep one\n"
    assert body == expected
    assert (Path(path, "review", "SKILL.md").stat().st_mode & 0o777) == 0o644


def test_a_symlinked_skills_directory_is_replaced_without_touching_its_target(state, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep\n")
    path = Path(state.workspace.root, "runs", "run-skills-link", "skills")
    path.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(str(outside), str(path))

    skills_path = state.workspace.prepare_skills_dir(
        "run-skills-link", [{"name": "review", "description": "d", "content": "c"}]
    )

    assert not Path(skills_path).is_symlink()
    assert (Path(skills_path) / "review" / "SKILL.md").read_text().startswith("---\nname: review\n")
    assert (outside / "keep.txt").read_text() == "keep\n"


def test_integrate_clean(state):
    repo = state.repo
    repo.sync()
    head, base = repo.ensure_branch("plane/ENG-13", "main")
    task = _task_commit(state, base, "one", {"one.txt": "1\n"})

    new = repo.integrate(task.sha, base, "plane/ENG-13", "integrate one", IDENTITY)
    assert new != head
    assert repo.head("plane/ENG-13") == new
    assert _rev_list_parents(repo, new) == [head]
    assert repo.changed_paths(head, new) == ["one.txt"]


def test_integrate_two_non_overlapping_tasks(state):
    repo = state.repo
    repo.sync()
    _, base = repo.ensure_branch("plane/ENG-14", "main")
    first = _task_commit(state, base, "first", {"one.txt": "1\n"})
    second = _task_commit(state, base, "second", {"two.txt": "2\n"})

    head = repo.integrate(first.sha, base, "plane/ENG-14", "first", IDENTITY)
    merged = repo.integrate(second.sha, base, "plane/ENG-14", "second", IDENTITY)
    assert merged != head
    listing = run_git(["ls-tree", "-r", "--name-only", merged], git_dir=repo.git_dir).stdout.decode()
    assert "one.txt" in listing
    assert "two.txt" in listing
    assert _rev_list_parents(repo, merged) == [head]


def test_integrate_conflict_reports_paths_and_tree(state):
    repo = state.repo
    repo.sync()
    _, base = repo.ensure_branch("plane/ENG-15", "main")
    first = _task_commit(state, base, "conflict-a", {"app.py": "print('a')\n"})
    second = _task_commit(state, base, "conflict-b", {"app.py": "print('b')\n"})
    head = repo.integrate(first.sha, base, "plane/ENG-15", "first", IDENTITY)

    with pytest.raises(MergeConflictError) as error:
        repo.integrate(second.sha, base, "plane/ENG-15", "second", IDENTITY)
    assert error.value.paths == ["app.py"]
    assert error.value.tree
    assert error.value.parents == [head]
    assert repo.head("plane/ENG-15") == head


def _conflict(state, branch):
    repo = state.repo
    repo.sync()
    _, base = repo.ensure_branch(branch, "main")
    first = _task_commit(state, base, "conflict-a", {"app.py": "print('a')\n"})
    second = _task_commit(state, base, "conflict-b", {"app.py": "print('b')\n"})
    repo.integrate(first.sha, base, branch, "first", IDENTITY)
    with pytest.raises(MergeConflictError) as captured:
        repo.integrate(second.sha, base, branch, "second", IDENTITY)
    return captured.value


def test_finish_conflict_success(state):
    repo = state.repo
    branch = "plane/ENG-16"
    conflict = _conflict(state, branch)
    head = repo.head(branch)

    work = state.workspace.prepare_work_dir("run", "tasks", "resolve")
    index = state.workspace.index_file("run", "tasks", "resolve")
    repo.checkout(head, work, index)
    Path(work, "app.py").write_text("print('resolved')\n")

    resolved = repo.finish_conflict(work, index, conflict, branch, "resolve", IDENTITY)
    assert resolved != head
    assert repo.head(branch) == resolved
    assert _rev_list_parents(repo, resolved) == [head]
    assert "resolved" in run_git(["cat-file", "blob", f"{resolved}:app.py"], git_dir=repo.git_dir).stdout.decode()


def test_finish_conflict_rejects_conflict_markers(state):
    repo = state.repo
    branch = "plane/ENG-17"
    conflict = _conflict(state, branch)
    head = repo.head(branch)

    work = state.workspace.prepare_work_dir("run", "tasks", "markers")
    index = state.workspace.index_file("run", "tasks", "markers")
    repo.checkout(head, work, index)
    Path(work, "app.py").write_text("<<<<<<< HEAD\nprint('a')\n=======\nprint('b')\n>>>>>>> plane/task\n")

    with pytest.raises(MergeConflictError) as error:
        repo.finish_conflict(work, index, conflict, branch, "bad resolve", IDENTITY)
    assert error.value.paths == ["app.py"]
    assert error.value.parents == conflict.parents
    assert repo.head(branch) == head


def test_finish_conflict_accepts_equals_rule_line(state):
    repo = state.repo
    branch = "plane/ENG-17b"
    conflict = _conflict(state, branch)
    head = repo.head(branch)

    work = state.workspace.prepare_work_dir("run", "tasks", "rule")
    index = state.workspace.index_file("run", "tasks", "rule")
    repo.checkout(head, work, index)
    Path(work, "app.py").write_text("Heading\n=======\nresolved\n")

    resolved = repo.finish_conflict(work, index, conflict, branch, "resolve", IDENTITY)
    assert repo.head(branch) == resolved
    assert repo.conflict_markers(resolved, ["app.py"]) == []


def _conflict_with_default_branch(state, branch):
    repo = state.repo
    repo.sync()
    _, base = repo.ensure_branch(branch, "main")
    task = _task_commit(state, base, "policy-a", {"app.py": "print('branch')\n"})
    run_git(["update-ref", f"refs/heads/{branch}", task.sha], git_dir=repo.git_dir)

    _advance_main(state, {"app.py": "print('main')\n", ".github/workflows/ci.yml": "on: push\n"})
    repo.sync()
    with pytest.raises(MergeConflictError) as captured:
        repo.merge_default(branch, "main", "merge main", IDENTITY)
    return captured.value


def test_finish_conflict_ignores_default_branch_changes(state):
    repo = state.repo
    branch = "plane/ENG-17c"
    conflict = _conflict_with_default_branch(state, branch)
    assert conflict.paths == ["app.py"]

    work = state.workspace.prepare_work_dir("run", "tasks", "policy-main")
    index = state.workspace.index_file("run", "tasks", "policy-main")
    repo.checkout(conflict.tree, work, index)
    assert Path(work, ".github", "workflows", "ci.yml").read_text() == "on: push\n"
    Path(work, "app.py").write_text("print('resolved')\n")

    resolved = repo.finish_conflict(work, index, conflict, branch, "resolve", IDENTITY)
    assert repo.head(branch) == resolved
    assert _rev_list_parents(repo, resolved) == conflict.parents
    listing = run_git(["ls-tree", "-r", "--name-only", resolved], git_dir=repo.git_dir).stdout.decode()
    assert ".github/workflows/ci.yml" in listing


def test_finish_conflict_rejects_workflow_change_during_resolution(state):
    repo = state.repo
    branch = "plane/ENG-17d"
    conflict = _conflict_with_default_branch(state, branch)
    head = repo.head(branch)

    work = state.workspace.prepare_work_dir("run", "tasks", "policy-agent")
    index = state.workspace.index_file("run", "tasks", "policy-agent")
    repo.checkout(conflict.tree, work, index)
    Path(work, "app.py").write_text("print('resolved')\n")
    Path(work, ".github", "workflows", "ci.yml").write_text("on: pull_request\n")

    with pytest.raises(AgentGitPolicyError) as error:
        repo.finish_conflict(work, index, conflict, branch, "resolve", IDENTITY)
    assert error.value.paths == [".github/workflows/ci.yml"]
    assert repo.head(branch) == head


def test_finish_conflict_rejects_stale_head(state):
    repo = state.repo
    branch = "plane/ENG-18"
    conflict = _conflict(state, branch)

    other = _task_commit(state, conflict.parents[0], "stale", {"other.txt": "x\n"})
    run_git(["update-ref", f"refs/heads/{branch}", other.sha], git_dir=repo.git_dir)

    work = state.workspace.prepare_work_dir("run", "tasks", "stale")
    index = state.workspace.index_file("run", "tasks", "stale")
    with pytest.raises(AgentGitError):
        repo.finish_conflict(work, index, conflict, branch, "stale", IDENTITY)
    assert repo.head(branch) == other.sha


def test_merge_default_clean(state):
    repo = state.repo
    repo.sync()
    branch = "plane/ENG-19"
    head, _ = repo.ensure_branch(branch, "main")

    _advance_main(state, {"main-file.txt": "from main\n"})
    repo.sync()
    default_sha = repo.resolve("refs/remotes/origin/main")

    merged = repo.merge_default(branch, "main", "merge main", IDENTITY)
    assert merged != head
    assert repo.head(branch) == merged
    assert _rev_list_parents(repo, merged) == [head, default_sha]
    listing = run_git(["ls-tree", "-r", "--name-only", merged], git_dir=repo.git_dir).stdout.decode()
    assert "main-file.txt" in listing
    assert repo.merge_default(branch, "main", "merge main again", IDENTITY) == merged


def test_merge_default_conflict(state):
    repo = state.repo
    repo.sync()
    branch = "plane/ENG-21"
    head, base = repo.ensure_branch(branch, "main")
    task = _task_commit(state, base, "main-conflict", {"app.py": "print('branch')\n"})
    run_git(["update-ref", f"refs/heads/{branch}", task.sha], git_dir=repo.git_dir)

    _advance_main(state, {"app.py": "print('main')\n"})
    repo.sync()
    default_sha = repo.resolve("refs/remotes/origin/main")

    with pytest.raises(MergeConflictError) as error:
        repo.merge_default(branch, "main", "merge main", IDENTITY)
    assert error.value.paths == ["app.py"]
    assert error.value.parents == [task.sha, default_sha]
    assert repo.head(branch) == task.sha
    assert head != task.sha


def test_push_and_refusals(state, monkeypatch):
    repo = state.repo
    repo.sync()
    branch = "plane/ENG-22"
    head, base = repo.ensure_branch(branch, "main")
    task = _task_commit(state, base, "push", {"pushed.txt": "1\n"})
    run_git(["update-ref", f"refs/heads/{branch}", task.sha], git_dir=repo.git_dir)

    calls = []
    real_run = subprocess.run

    def spy(*args, **kwargs):
        calls.append(list(args[0]))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    sha = repo.push(branch)
    assert sha == task.sha
    assert repo.resolve(f"refs/remotes/origin/{branch}") == task.sha
    remote_sha = run_git(["rev-parse", f"refs/heads/{branch}"], git_dir=str(state.remote)).stdout.decode().strip()
    assert remote_sha == task.sha
    push_argv = next(argv for argv in calls if "push" in argv)
    assert "--force" not in push_argv
    assert "-f" not in push_argv

    with pytest.raises(AgentGitError):
        repo.push("feature/nope")

    divergent = _task_commit(state, base, "divergent", {"divergent.txt": "1\n"})
    run_git(["update-ref", f"refs/heads/{branch}", divergent.sha], git_dir=repo.git_dir)
    with pytest.raises(AgentGitError):
        repo.push(branch)
    still = run_git(["rev-parse", f"refs/heads/{branch}"], git_dir=str(state.remote)).stdout.decode().strip()
    assert still == task.sha
    assert head != divergent.sha
