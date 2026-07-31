#!/usr/bin/env bash
# Batch 4: STE v2 (adds the "Do not lose information" rule) on the same targets,
# to check that conformance holds while citation and quantity density recovers.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
# Where the copied audit targets live. Copy them OUT of this repo: an agent
# auditing a target inside pipeline-workspace/ can read ground_truth.json.
SP="${TARGETS_DIR:?set TARGETS_DIR to the dir holding the copied targets}"
RO="Read Grep Glob"

"$IT/run_skill.sh" sc-eventbus-ste2  "$REPO/spring_clean/SKILL.md" "" "$SP/targets/eventbus" "$RO" &
"$IT/run_skill.sh" apc-eventbus-ste2 "$REPO/address-pr-comments/SKILL.md" "review-comments.md" "$SP/targets/eventbus-apc" "$RO" &
"$IT/run_skill.sh" pr-private-ste2 "$REPO/pr-review/SKILL.md" "${PR_NUMBER:?set PR_NUMBER}" "${PR_REPO_DIR:?set PR_REPO_DIR}" "Read Grep Glob Bash" &
wait
echo "BATCH4 COMPLETE"
