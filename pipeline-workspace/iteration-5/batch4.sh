#!/usr/bin/env bash
# Batch 4: STE v2 (adds the "Do not lose information" rule) on the same targets,
# to check that conformance holds while citation and quantity density recovers.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
SP=/private/tmp/claude-501/-Users-briant-Documents-Programming-skill-me/81b2b2aa-ea01-414f-bcb7-1df8d3566a40/scratchpad
RO="Read Grep Glob"

"$IT/run_skill.sh" sc-eventbus-ste2  "$REPO/spring_clean/SKILL.md" "" "$SP/targets/eventbus" "$RO" &
"$IT/run_skill.sh" apc-eventbus-ste2 "$REPO/address-pr-comments/SKILL.md" "review-comments.md" "$SP/targets/eventbus-apc" "$RO" &
"$IT/run_skill.sh" pr-private-ste2 "$REPO/pr-review/SKILL.md" "${PR_NUMBER:?set PR_NUMBER}" "${PR_REPO_DIR:?set PR_REPO_DIR}" "Read Grep Glob Bash" &
wait
echo "BATCH4 COMPLETE"
