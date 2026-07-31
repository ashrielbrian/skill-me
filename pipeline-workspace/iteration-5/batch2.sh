#!/usr/bin/env bash
# Batch 2: pr-review, both arms, against a real small PR in a private repo.
# Set PR_REPO_DIR and PR_NUMBER for the target; artifacts under runs/pr-private*
# are gitignored, because this repo is public.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
# Where the copied audit targets live. Copy them OUT of this repo: an agent
# auditing a target inside pipeline-workspace/ can read ground_truth.json.
SP="${TARGETS_DIR:?set TARGETS_DIR to the dir holding the copied targets}"
TOOLS="Read Grep Glob Bash"

"$IT/run_skill.sh" pr-private-ste  "$REPO/pr-review/SKILL.md" "${PR_NUMBER:?set PR_NUMBER}" "${PR_REPO_DIR:?set PR_REPO_DIR}" "$TOOLS" &
"$IT/run_skill.sh" pr-private-base "$IT/skills-baseline/pr-review/SKILL.md" "${PR_NUMBER:?set PR_NUMBER}" "${PR_REPO_DIR:?set PR_REPO_DIR}" "$TOOLS" &
wait
echo "BATCH2 COMPLETE"
