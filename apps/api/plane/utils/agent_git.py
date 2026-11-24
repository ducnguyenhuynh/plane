# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

"""Git engine for AI agent workspaces.

The agent edits a plain directory and controls every byte in it, so the real git
directory is a bare repository outside that directory. Every git command runs
with an explicit ``--git-dir`` (plus ``--work-tree``/``GIT_INDEX_FILE`` when a
tree is involved) and a minimal environment that never inherits Plane secrets.
"""

# Python imports
import base64
import fcntl
import json
import os
import posixpath
import re
import shutil
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass

SANDBOX_UID = 10001
BRANCH_PREFIX = "plane/"
BLOCKED_PATHS = (".github/workflows/", ".github/actions/", ".gitmodules")
# Installed dependencies are never committed unless they were already tracked
DEPENDENCY_EXCLUDES = ("node_modules/", ".venv/", "__pycache__/", ".pytest_cache/", ".cache/")

TOKEN_CONFIG_KEY = "http.https://github.com/.extraheader"

# Always applied; argv only, no shell.
SAFETY_CONFIG = (
    "core.hooksPath=/dev/null",
    "core.fsmonitor=false",
    "core.symlinks=true",
    "core.protectHFS=true",
    "core.protectNTFS=true",
    "protocol.ext.allow=never",
    "credential.helper=",
    "http.followRedirects=false",
    "gc.auto=0",
    "commit.gpgsign=false",
)

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_IDENTIFIER_RE = re.compile(r"^[ÇŞĞIİÖÜA-Za-z0-9]+$")
_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_WORK_KINDS = ("tasks", "review", "plan")
_CONFLICT_MARKERS = (b"<<<<<<< ", b">>>>>>> ")


class AgentGitError(Exception):
    pass


class AgentGitPolicyError(AgentGitError):
    def __init__(self, message, paths):
        self.paths = list(paths)
        super().__init__(message)


class MergeConflictError(AgentGitError):
    def __init__(self, tree, paths, parents, message=None):
        self.tree = tree
        self.paths = list(paths)
        self.parents = list(parents)
        super().__init__(message or f"merge conflict in {len(self.paths)} path(s)")


class BranchDivergedError(AgentGitError):
    pass


@dataclass(frozen=True)
class GitIdentity:
    name: str
    email: str


@dataclass(frozen=True)
class CommitResult:
    sha: str | None
    paths: list[str]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def validate_repository(full_name):
    if not isinstance(full_name, str) or not _REPOSITORY_RE.match(full_name):
        raise AgentGitError(f"invalid repository name: {full_name!r}")
    for part in full_name.split("/"):
        if part in (".", ".."):
            raise AgentGitError(f"invalid repository name: {full_name!r}")
        if part.endswith(".git"):
            raise AgentGitError(f"invalid repository name: {full_name!r}")
    return full_name


def repository_url(full_name):
    return f"https://github.com/{validate_repository(full_name)}.git"


def branch_for_issue(identifier, sequence_id):
    if not isinstance(identifier, str) or not _IDENTIFIER_RE.match(identifier):
        raise AgentGitError(f"invalid issue identifier: {identifier!r}")
    return f"{BRANCH_PREFIX}{identifier}-{sequence_id}"


def _auth_base64(token):
    return base64.b64encode(f"x-access-token:{token}".encode()).decode()


def _auth_header(token):
    return f"Authorization: Basic {_auth_base64(token)}"


def _scrub(text, token):
    if not text or not token:
        return text
    text = text.replace(_auth_base64(token), "***")
    return text.replace(token, "***")


def _decode(data):
    return data.decode("utf-8", errors="replace")


def _base_env():
    # Allowlist only: os.environ is never inherited.
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "HOME": "/nonexistent",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def run_git(
    args,
    *,
    git_dir=None,
    work_tree=None,
    index_file=None,
    token=None,
    input_bytes=None,
    env_extra=None,
    check=True,
    timeout=900,
):
    command = ["git"]
    for item in SAFETY_CONFIG:
        command += ["-c", item]
    if git_dir is not None:
        command += ["--git-dir", str(git_dir)]
    if work_tree is not None:
        command += ["--work-tree", str(work_tree)]
    command += [str(arg) for arg in args]

    env = _base_env()
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    if token:
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = TOKEN_CONFIG_KEY
        env["GIT_CONFIG_VALUE_0"] = _auth_header(token)
    if env_extra:
        env.update({str(key): str(value) for key, value in env_extra.items()})

    try:
        # cwd outside every agent directory: git must never discover a repository.
        result = subprocess.run(
            command,
            cwd="/",
            env=env,
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        raise AgentGitError(_scrub(f"git timed out after {timeout}s", token)) from error
    except OSError as error:
        raise AgentGitError(_scrub(f"git could not start: {error}", token)) from error

    if check and result.returncode != 0:
        detail = _decode(result.stderr).strip() or _decode(result.stdout).strip()
        if not detail:
            detail = f"git exited with code {result.returncode}"
        raise AgentGitError(_scrub(detail, token))
    return result


def clear_directory(path):
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.unlink(entry.path)


def remove_git_entries(path):
    for dirpath, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        for name in list(dirnames):
            if name != ".git":
                continue
            target = os.path.join(dirpath, name)
            if os.path.islink(target) or not os.path.isdir(target):
                os.unlink(target)
            else:
                shutil.rmtree(target)
            dirnames.remove(name)
        for name in filenames:
            if name != ".git":
                continue
            target = os.path.join(dirpath, name)
            if os.path.islink(target) or not os.path.isdir(target):
                os.unlink(target)
            else:
                shutil.rmtree(target)


def _safe_component(value, label):
    text = str(value)
    if not text or text in (".", "..") or os.sep in text or "\\" in text or (os.altsep and os.altsep in text):
        raise AgentGitError(f"invalid {label}: {value!r}")
    return text


def _chown_tree(path):
    if os.geteuid() != 0:
        return
    for dirpath, dirnames, filenames in os.walk(path, topdown=False, followlinks=False):
        for name in dirnames + filenames:
            try:
                os.lchown(os.path.join(dirpath, name), SANDBOX_UID, -1)
            except OSError:
                pass
    try:
        os.lchown(path, SANDBOX_UID, -1)
    except OSError:
        pass


def _parse_raw_diff(data):
    """Parse ``diff-tree -r -z --raw`` output: records alternate meta, path."""
    parts = data.split(b"\0")
    entries = []
    index = 0
    while index + 1 < len(parts):
        meta, path = parts[index], parts[index + 1]
        index += 2
        fields = meta.lstrip(b":").split(b" ")
        if len(fields) < 5:
            continue
        old_mode, new_mode, _old_sha, new_sha, status = fields[:5]
        entries.append(
            {
                "old_mode": _decode(old_mode),
                "new_mode": _decode(new_mode),
                "new_sha": _decode(new_sha),
                "status": _decode(status)[:1],
                "path": os.fsdecode(path),
            }
        )
    return entries


def _parse_merge_tree(data):
    parts = data.split(b"\0")
    tree = _decode(parts[0]).strip() if parts else ""
    paths = [_decode(part) for part in parts[1:] if part]
    return tree, paths


def _is_blocked(path):
    for blocked in BLOCKED_PATHS:
        if path == blocked.rstrip("/") or path.startswith(blocked):
            return True
    return False


# ---------------------------------------------------------------------------
# workspace
# ---------------------------------------------------------------------------


class AgentWorkspace:
    def __init__(self, root=None):
        self.root = os.path.abspath(root or os.environ.get("AGENT_WORKSPACE_ROOT") or "/agent-workspaces")

    def _repo_key(self, full_name):
        owner, repo = validate_repository(full_name).split("/")
        return f"{owner}__{repo}"

    def repo_dir(self, full_name):
        return os.path.join(self.root, "repos", f"{self._repo_key(full_name)}.git")

    def lock_path(self, full_name):
        return os.path.join(self.root, "locks", f"{self._repo_key(full_name)}.lock")

    def work_dir(self, run_id, kind, key):
        if kind not in _WORK_KINDS:
            raise ValueError(f"invalid work kind: {kind!r}")
        return os.path.join(
            self.root,
            "runs",
            _safe_component(run_id, "run id"),
            kind,
            _safe_component(key, "key"),
        )

    def index_file(self, run_id, kind, key):
        if kind not in _WORK_KINDS:
            raise ValueError(f"invalid work kind: {kind!r}")
        return os.path.join(
            self.root,
            "runs",
            _safe_component(run_id, "run id"),
            "index",
            f"{kind}-{_safe_component(key, 'key')}",
        )

    def sessions_dir(self, issue_id):
        return os.path.join(self.root, "sessions", _safe_component(issue_id, "issue id"))

    def relative(self, path):
        return os.path.relpath(str(path), self.root)

    def prepare_work_dir(self, run_id, kind, key):
        path = self.work_dir(run_id, kind, key)
        os.makedirs(path, exist_ok=True)
        clear_directory(path)
        os.chmod(path, 0o755)
        if os.geteuid() == 0:
            try:
                os.chown(path, SANDBOX_UID, -1)
            except OSError:
                pass
        return path

    def prepare_sessions_dir(self, issue_id):
        path = self.sessions_dir(issue_id)
        os.makedirs(path, exist_ok=True)
        os.chmod(path, 0o700)
        _chown_tree(path)
        return path

    def prepare_cache_dir(self, project_id):
        path = os.path.join(self.root, "caches", _safe_component(project_id, "project id"))
        os.makedirs(path, exist_ok=True)
        os.chmod(path, 0o700)
        if os.geteuid() == 0:
            try:
                os.chown(path, SANDBOX_UID, -1)
            except OSError:
                pass
        return path

    def prepare_skills_dir(self, run_id, skills):
        """Root-owned skill files the sandbox may read but never write."""
        path = os.path.join(self.root, "runs", _safe_component(run_id, "run id"), "skills")
        if os.path.islink(path):
            os.unlink(path)
        elif os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.unlink(path)
        os.makedirs(path, mode=0o755)
        os.chmod(path, 0o755)

        for skill in skills:
            name = skill.get("name") if isinstance(skill, dict) else None
            if not isinstance(name, str) or not _SKILL_NAME_RE.fullmatch(name):
                raise AgentGitError(f"invalid skill name: {name!r}")
            directory = os.path.join(path, name)
            os.makedirs(directory, mode=0o755, exist_ok=True)
            os.chmod(directory, 0o755)
            content = (
                "---\n"
                f"name: {name}\n"
                f"description: {json.dumps(skill.get('description') or '')}\n"
                "---\n\n"
                f"{skill.get('content') or ''}\n"
            )
            # O_EXCL + O_NOFOLLOW: never write through a file (or link) that already exists
            fd = os.open(
                os.path.join(directory, "SKILL.md"),
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o644,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
        return path

    def cleanup_run(self, run_id):
        target = os.path.join(self.root, "runs", _safe_component(run_id, "run id"))
        if os.path.islink(target):
            os.unlink(target)
        elif os.path.isdir(target):
            shutil.rmtree(target)


# ---------------------------------------------------------------------------
# repository
# ---------------------------------------------------------------------------


class AgentRepository:
    def __init__(self, workspace, full_name, token=None, remote_url=None):
        self.workspace = workspace
        self.full_name = validate_repository(full_name)
        self.token = token
        self.remote_url = remote_url or repository_url(self.full_name)
        self.git_dir = workspace.repo_dir(self.full_name)

    # -- plumbing ---------------------------------------------------------

    @contextmanager
    def lock(self):
        path = self.workspace.lock_path(self.full_name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handle = open(path, "a+b")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def _git(self, args, **kwargs):
        kwargs.setdefault("git_dir", self.git_dir)
        return run_git(args, **kwargs)

    def _is_ancestor(self, ancestor, descendant):
        result = self._git(["merge-base", "--is-ancestor", ancestor, descendant], check=False)
        return result.returncode == 0

    def _tree_of(self, revision):
        result = self._git(["rev-parse", "--verify", f"{revision}^{{tree}}"])
        return _decode(result.stdout).strip()

    def _commit_tree(self, tree, parents, message, identity):
        args = ["commit-tree", tree]
        for parent in parents:
            args += ["-p", parent]
        args += ["-m", message]
        env_extra = {
            "GIT_AUTHOR_NAME": identity.name,
            "GIT_AUTHOR_EMAIL": identity.email,
            "GIT_COMMITTER_NAME": identity.name,
            "GIT_COMMITTER_EMAIL": identity.email,
        }
        result = self._git(args, env_extra=env_extra, timeout=120)
        return _decode(result.stdout).strip()

    def _update_ref(self, branch, new_sha, old_sha):
        self._git(["update-ref", f"refs/heads/{branch}", new_sha, old_sha])

    def _diff_entries(self, base, tree):
        result = self._git(["diff-tree", "-r", "-z", "--raw", "--no-renames", base, tree])
        return _parse_raw_diff(result.stdout)

    def _agent_entries(self, tree, parents):
        """Entries whose path differs from every parent: only the agent's own work.

        A merge commit's other parents brought legitimate changes (e.g. a workflow
        file on the default branch) that policy must not judge.
        """
        entries = self._diff_entries(parents[0], tree)
        if len(parents) == 1:
            return entries
        shared = None
        for parent in parents[1:]:
            paths = {entry["path"] for entry in self._diff_entries(parent, tree)}
            shared = paths if shared is None else shared & paths
        return [entry for entry in entries if entry["path"] in shared]

    def _check_policy(self, entries):
        violations = []
        for entry in entries:
            path = entry["path"]
            if _is_blocked(path):
                violations.append(path)
            if "160000" in (entry["old_mode"], entry["new_mode"]):
                violations.append(path)
                continue
            if entry["new_mode"] != "120000" or entry["status"] == "D":
                continue
            if path == ".github":
                violations.append(path)
                continue
            result = self._git(["cat-file", "blob", entry["new_sha"]], check=False)
            if result.returncode != 0:
                continue
            target = os.fsdecode(result.stdout)
            if os.path.isabs(target):
                violations.append(path)
                continue
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            if resolved == ".." or resolved.startswith("../") or os.path.isabs(resolved):
                violations.append(path)
        if violations:
            unique = sorted(set(violations))
            raise AgentGitPolicyError(f"blocked paths in agent commit: {', '.join(unique)}", unique)

    # -- public API -------------------------------------------------------

    def sync(self):
        with self.lock():
            if not os.path.exists(os.path.join(self.git_dir, "HEAD")):
                run_git(["init", "--bare", "--quiet", self.git_dir], timeout=120)
            info_dir = os.path.join(self.git_dir, "info")
            os.makedirs(info_dir, exist_ok=True)
            with open(os.path.join(info_dir, "exclude"), "w", encoding="utf-8") as handle:
                handle.writelines(f"{pattern}\n" for pattern in DEPENDENCY_EXCLUDES)
            self._git(
                [
                    "fetch",
                    "--prune",
                    "--no-tags",
                    self.remote_url,
                    "+refs/heads/*:refs/remotes/origin/*",
                ],
                token=self.token,
            )

    def resolve(self, ref):
        result = self._git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], check=False)
        if result.returncode != 0:
            return None
        sha = _decode(result.stdout).strip()
        return sha or None

    def ensure_branch(self, branch, default_branch, resume=False):
        _validate_branch(branch)
        _validate_branch(default_branch, prefix=False)

        local_ref = f"refs/heads/{branch}"
        remote_ref = f"refs/remotes/origin/{branch}"
        default_sha = self.resolve(f"refs/remotes/origin/{default_branch}")
        if default_sha is None:
            raise AgentGitError(f"default branch not found: {default_branch}")

        remote_sha = self.resolve(remote_ref)
        local_sha = self.resolve(local_ref)
        if not resume:
            # A new run starts from GitHub: unpushed leftovers are discarded.
            local_sha = remote_sha or default_sha
            self._git(["update-ref", local_ref, local_sha])
        elif remote_sha is None:
            if local_sha is None:
                local_sha = default_sha
                self._git(["update-ref", local_ref, local_sha])
        elif local_sha is None or self._is_ancestor(local_sha, remote_sha):
            local_sha = remote_sha
            self._git(["update-ref", local_ref, local_sha])
        elif not self._is_ancestor(remote_sha, local_sha):
            raise BranchDivergedError(f"branch {branch} diverged from GitHub; update it or start a new run")

        base = self._merge_base(local_sha, default_sha) or default_sha
        return local_sha, base

    def _merge_base(self, left, right):
        result = self._git(["merge-base", left, right], check=False)
        if result.returncode != 0:
            return None
        return _decode(result.stdout).strip() or None

    def merge_base(self, left, right):
        return self._merge_base(left, right)

    def head(self, branch):
        sha = self.resolve(f"refs/heads/{branch}")
        if sha is None:
            raise AgentGitError(f"branch not found: {branch}")
        return sha

    def checkout(self, sha_or_tree, work_dir, index_file):
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.dirname(str(index_file)), exist_ok=True)
        clear_directory(work_dir)
        self._git(
            ["read-tree", "--reset", "-u", sha_or_tree],
            work_tree=work_dir,
            index_file=index_file,
            timeout=300,
        )
        _chown_tree(work_dir)

    def capture(self, work_dir, index_file, parents, message, identity, diff_base=None):
        if not parents:
            raise AgentGitError("capture requires at least one parent")
        os.makedirs(os.path.dirname(str(index_file)), exist_ok=True)
        remove_git_entries(work_dir)
        self._git(["read-tree", parents[0]], index_file=index_file)
        self._git(
            ["add", "-A", "--", "."],
            work_tree=work_dir,
            index_file=index_file,
            timeout=300,
        )
        result = self._git(["write-tree"], index_file=index_file)
        tree = _decode(result.stdout).strip()

        entries = self._diff_entries(diff_base or parents[0], tree)
        self._check_policy(self._agent_entries(tree, parents))

        if not entries and len(parents) == 1:
            return CommitResult(None, [])
        paths = [entry["path"] for entry in entries]
        sha = self._commit_tree(tree, parents, message, identity)
        return CommitResult(sha, paths)

    def integrate(self, task_commit, task_base, branch, message, identity):
        _validate_branch(branch)
        with self.lock():
            head_sha = self.head(branch)
            result = self._git(
                [
                    "merge-tree",
                    "--write-tree",
                    "-z",
                    "--name-only",
                    "--no-messages",
                    f"--merge-base={task_base}",
                    head_sha,
                    task_commit,
                ],
                check=False,
            )
            tree, paths = _parse_merge_tree(result.stdout)
            if result.returncode == 0:
                if tree == self._tree_of(head_sha):
                    return head_sha
                new_sha = self._commit_tree(tree, [head_sha], message, identity)
                self._update_ref(branch, new_sha, head_sha)
                return new_sha
            if result.returncode == 1:
                raise MergeConflictError(tree, paths, [head_sha])
            detail = _decode(result.stderr).strip() or "merge-tree failed"
            raise AgentGitError(_scrub(detail, self.token))

    def merge_default(self, branch, default_branch, message, identity):
        _validate_branch(branch)
        with self.lock():
            head_sha = self.head(branch)
            default_sha = self.resolve(f"refs/remotes/origin/{default_branch}")
            if default_sha is None:
                raise AgentGitError(f"default branch not found: {default_branch}")
            if self._is_ancestor(default_sha, head_sha):
                return head_sha
            result = self._git(
                [
                    "merge-tree",
                    "--write-tree",
                    "-z",
                    "--name-only",
                    "--no-messages",
                    head_sha,
                    default_sha,
                ],
                check=False,
            )
            tree, paths = _parse_merge_tree(result.stdout)
            if result.returncode == 0:
                new_sha = self._commit_tree(tree, [head_sha, default_sha], message, identity)
                self._update_ref(branch, new_sha, head_sha)
                return new_sha
            if result.returncode == 1:
                raise MergeConflictError(tree, paths, [head_sha, default_sha])
            detail = _decode(result.stderr).strip() or "merge-tree failed"
            raise AgentGitError(_scrub(detail, self.token))

    def finish_conflict(self, work_dir, index_file, conflict, branch, message, identity):
        _validate_branch(branch)
        with self.lock():
            current = self.head(branch)
            if conflict.parents[0] != current:
                raise AgentGitError("branch head moved since the conflict was created")
            result = self.capture(
                work_dir,
                index_file,
                list(conflict.parents),
                message,
                identity,
                diff_base=conflict.parents[0],
            )
            if result.sha is None:
                return current
            markers = self.conflict_markers(result.sha, conflict.paths)
            if markers:
                raise MergeConflictError(result.sha, markers, list(conflict.parents))
            self._update_ref(branch, result.sha, current)
            return result.sha

    def conflict_markers(self, commit, paths):
        # "=======" alone is valid Markdown/reStructuredText, so it is not a marker.
        found = []
        for path in paths:
            result = self._git(["cat-file", "blob", f"{commit}:{path}"], check=False)
            if result.returncode != 0:
                continue
            for line in result.stdout.splitlines():
                if line.startswith(_CONFLICT_MARKERS):
                    found.append(path)
                    break
        return found

    def push(self, branch):
        _validate_branch(branch)
        with self.lock():
            sha = self.head(branch)
            self._git(
                ["push", self.remote_url, f"{sha}:refs/heads/{branch}"],
                token=self.token,
            )
            self._git(["update-ref", f"refs/remotes/origin/{branch}", sha])
            return sha

    def diff(self, base, head):
        result = self._git(
            ["diff", "--no-color", "--no-ext-diff", "--no-textconv", base, head],
        )
        return _decode(result.stdout)

    def changed_paths(self, base, head):
        return [entry["path"] for entry in self._diff_entries(base, head)]


def _validate_branch(branch, prefix=True):
    if not isinstance(branch, str) or not branch:
        raise AgentGitError(f"invalid branch name: {branch!r}")
    if prefix and not branch.startswith(BRANCH_PREFIX):
        raise AgentGitError(f"branch must start with {BRANCH_PREFIX}: {branch!r}")
    result = run_git(["check-ref-format", "--branch", branch], check=False)
    if result.returncode != 0:
        raise AgentGitError(f"invalid branch name: {branch!r}")
