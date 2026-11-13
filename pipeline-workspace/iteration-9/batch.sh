#!/usr/bin/env bash
# Subtle tier: PR #3 (bulk) against pr-review before and after the iteration-8
# subtraction, and the native /code-review. Two samples per arm.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
I8="$IT/../iteration-8"
FX="${FIXTURE_DIR:?set FIXTURE_DIR to a scratch dir outside this repo}"
export RUNS_DIR="$IT/runs"
for s in a b; do
    "$I8/run_skill.sh" "base-pr3-$s"   "$I8/skills-baseline/pr-review/SKILL.md" 3 "$FX" &
    "$I8/run_skill.sh" "cur-pr3-$s"    "$IT/../../pr-review/SKILL.md" 3 "$FX" &
    "$I8/run_skill.sh" "native-pr3-$s" native:high 3 "$FX" &
done
wait
echo "BATCH COMPLETE"
