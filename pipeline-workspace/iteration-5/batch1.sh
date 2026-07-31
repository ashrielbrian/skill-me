#!/usr/bin/env bash
# Batch 1: spring_clean + address-pr-comments, both arms, in parallel.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
# Where the copied audit targets live. Copy them OUT of this repo: an agent
# auditing a target inside pipeline-workspace/ can read ground_truth.json.
SP="${TARGETS_DIR:?set TARGETS_DIR to the dir holding the copied targets}"
CUR="$REPO"
BASE="$IT/skills-baseline"
RO="Read Grep Glob"

"$IT/run_skill.sh" sc-eventbus-ste  "$CUR/spring_clean/SKILL.md" "" "$SP/targets/eventbus" "$RO" &
"$IT/run_skill.sh" sc-eventbus-base "$BASE/spring_clean/SKILL.md" "" "$SP/targets/eventbus" "$RO" &
"$IT/run_skill.sh" apc-eventbus-ste  "$CUR/address-pr-comments/SKILL.md" "review-comments.md" "$SP/targets/eventbus-apc" "$RO" &
"$IT/run_skill.sh" apc-eventbus-base "$BASE/address-pr-comments/SKILL.md" "review-comments.md" "$SP/targets/eventbus-apc" "$RO" &
wait
echo "BATCH1 COMPLETE"
