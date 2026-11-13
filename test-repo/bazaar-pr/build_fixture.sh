#!/usr/bin/env bash
# Materialize the bazaar PR fixture: test-repo/bazaar as main, plus the PR
# overlays in this directory as origin branches and refs/pull/N/head.
# Usage: build_fixture.sh <dest-dir>
# Put ../parcelpost/bin first on PATH so `gh` resolves to the shim.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="$1"
rm -rf "$DEST"; mkdir -p "$DEST"
git init -q --bare "$DEST/origin.git"
git init -q -b main "$DEST/work"
cd "$DEST/work"

export GIT_AUTHOR_NAME="Dana R" GIT_AUTHOR_EMAIL="dana@example.com"
export GIT_COMMITTER_NAME="Dana R" GIT_COMMITTER_EMAIL="dana@example.com"
export GIT_AUTHOR_DATE="2026-10-01T09:00:00Z" GIT_COMMITTER_DATE="2026-10-01T09:00:00Z"

rsync -a --exclude __pycache__ "$SRC/../bazaar/" .
git add -A && git commit -q -m "bazaar 2.11.0"

git checkout -q -b order-refunds main
rsync -a --exclude __pycache__ "$SRC/pr-1/" .
git add -A && git commit -q -m "checkout: full-order refunds"
git checkout -q main

git remote add origin "$DEST/origin.git"
git push -q origin main order-refunds
git push -q origin order-refunds:refs/pull/1/head
git branch -q -D order-refunds
git fetch -q origin
git branch -q --set-upstream-to=origin/main main

cp "$SRC/prs.json" .git/gh-shim.json
: > .git/gh-shim.log
echo "fixture ready: $DEST/work"
