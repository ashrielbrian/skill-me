#!/usr/bin/env bash
# Batch 2: pr-review, both arms, against a real small PR in a private repo.
# Set PR_REPO_DIR and PR_NUMBER for the target; artifacts under runs/pr-private*
# are gitignored, because this repo is public.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
SP=/private/tmp/claude-501/-Users-briant-Documents-Programming-skill-me/81b2b2aa-ea01-414f-bcb7-1df8d3566a40/scratchpad
TOOLS="Read Grep Glob Bash"

"$IT/run_skill.sh" pr-private-ste  "$REPO/pr-review/SKILL.md" "${PR_NUMBER:?set PR_NUMBER}" "${PR_REPO_DIR:?set PR_REPO_DIR}" "$TOOLS" &
"$IT/run_skill.sh" pr-private-base "$IT/skills-baseline/pr-review/SKILL.md" "${PR_NUMBER:?set PR_NUMBER}" "${PR_REPO_DIR:?set PR_REPO_DIR}" "$TOOLS" &
wait
echo "BATCH2 COMPLETE"
