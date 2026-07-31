#!/usr/bin/env bash
# Batch 1: spring_clean + address-pr-comments, both arms, in parallel.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
SP=/private/tmp/claude-501/-Users-briant-Documents-Programming-skill-me/81b2b2aa-ea01-414f-bcb7-1df8d3566a40/scratchpad
CUR="$REPO"
BASE="$IT/skills-baseline"
RO="Read Grep Glob"

"$IT/run_skill.sh" sc-eventbus-ste  "$CUR/spring_clean/SKILL.md" "" "$SP/targets/eventbus" "$RO" &
"$IT/run_skill.sh" sc-eventbus-base "$BASE/spring_clean/SKILL.md" "" "$SP/targets/eventbus" "$RO" &
"$IT/run_skill.sh" apc-eventbus-ste  "$CUR/address-pr-comments/SKILL.md" "review-comments.md" "$SP/targets/eventbus-apc" "$RO" &
"$IT/run_skill.sh" apc-eventbus-base "$BASE/address-pr-comments/SKILL.md" "review-comments.md" "$SP/targets/eventbus-apc" "$RO" &
wait
echo "BATCH1 COMPLETE"
