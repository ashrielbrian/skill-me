#!/usr/bin/env bash
# Bazaar fan-out differential: solo (no subagents) vs fanout (Task/Agent allowed),
# plus a perfburn regression guard. Usage:
#   TARGETS_DIR=/path/to/scratch [ARM=v3] [RUNS="solo fanout guard"] batch-bazaar.sh
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
# Copy targets OUT of this repo: an agent auditing a target inside
# pipeline-workspace/ can read the ground-truth files.
SP="${TARGETS_DIR:?set TARGETS_DIR to the dir holding the copied targets}"
ARM="${ARM:-v3}"
SKILL_MD="${SKILL_MD:-$REPO/perform-like-jeff-dean/SKILL.md}"
RO="Read Grep Glob"
FAN="Read Grep Glob Task Agent"
NONMUTATING="Write Edit NotebookEdit"
RUNS="${RUNS:-solo fanout guard}"

for r in $RUNS; do
    case "$r" in
        solo)   "$IT/run_skill.sh" "perf-bazaar-$ARM-solo"   "$SKILL_MD" "" "$SP/targets/bazaar"   "$RO" & ;;
        fanout) "$IT/run_skill.sh" "perf-bazaar-$ARM-fanout" "$SKILL_MD" "" "$SP/targets/bazaar"   "$FAN" "$NONMUTATING" & ;;
        guard)  "$IT/run_skill.sh" "perf-perfburn-$ARM-g"    "$SKILL_MD" "" "$SP/targets/perfburn" "$RO" & ;;
    esac
done
wait
echo "BATCH-BAZAAR COMPLETE ($ARM: $RUNS)"
