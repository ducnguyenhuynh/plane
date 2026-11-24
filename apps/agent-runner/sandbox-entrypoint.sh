#!/bin/sh
set -eu

# Give the agent a disposable git baseline so `git status` and `git diff` work
# without it ever touching a repository history. Never fatal.
if [ -w /workspace ] && [ ! -d /workspace/.git ]; then
  (
    cd /workspace
    git init -q -b sandbox
    git add -A
    git -c user.name=sandbox -c user.email=sandbox@plane.invalid \
      commit -q --allow-empty --no-verify -m baseline
  ) || true
fi

exec opencode run --standalone --format json --auto --thinking "$@"
