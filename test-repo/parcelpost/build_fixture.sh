#!/usr/bin/env bash
# Materialize the parcelpost PR fixture as a git checkout with a local "origin".
# Usage: build_fixture.sh <dest-dir>
#
# <dest>/work is the checkout a reviewer runs in (on main; the PR branches exist
# only as origin/* and refs/pull/N/head, as they do for a reviewer who did not
# write the PR). Put bin/ first on PATH so `gh` resolves to the shim.
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

cp -R "$SRC/base/." .
git add -A && git commit -q -m "parcelpost 0.4.0"

commit_pr() {  # <overlay-dir> <branch> <message>
    git checkout -q -b "$2" main
    cp -R "$SRC/$1/." .
    git add -A && git commit -q -m "$3"
    git checkout -q main
}
commit_pr pr-1 refunds "Add shipment refunds"
commit_pr pr-2 limit-cap "Cap the listing page size at 100"

git remote add origin "$DEST/origin.git"
git push -q origin main refunds limit-cap
git push -q origin refunds:refs/pull/1/head limit-cap:refs/pull/2/head
git branch -q -D refunds limit-cap
git fetch -q origin
git branch -q --set-upstream-to=origin/main main

# The shim reads PR metadata from inside .git so it is not part of the tree under review.
cp "$SRC/prs.json" .git/gh-shim.json
: > .git/gh-shim.log
echo "fixture ready: $DEST/work"
