#!/usr/bin/env bash
# perform-like-jeff-dean eval: both targets, stub baseline + skill arm, in parallel.
# Usage: TARGETS_DIR=/path/to/scratch [ARM=v1] [SKILL_MD=...] batch-perf.sh
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
# Where the copied audit targets live. Copy them OUT of this repo: an agent
# auditing a target inside pipeline-workspace/ can read ground_truth.json.
SP="${TARGETS_DIR:?set TARGETS_DIR to the dir holding the copied targets}"
ARM="${ARM:-v1}"
SKILL_MD="${SKILL_MD:-$REPO/perform-like-jeff-dean/SKILL.md}"
RO="Read Grep Glob"

if [ "${RUN_BASE:-1}" = "1" ]; then
    "$IT/run_skill.sh" perf-perfburn-base "$IT/stub-perf.md" "" "$SP/targets/perfburn" "$RO" &
    "$IT/run_skill.sh" perf-eventbus-base "$IT/stub-perf.md" "" "$SP/targets/eventbus" "$RO" &
fi
"$IT/run_skill.sh" "perf-perfburn-$ARM" "$SKILL_MD" "" "$SP/targets/perfburn" "$RO" &
"$IT/run_skill.sh" "perf-eventbus-$ARM" "$SKILL_MD" "" "$SP/targets/eventbus" "$RO" &
wait
echo "BATCH-PERF COMPLETE ($ARM)"
