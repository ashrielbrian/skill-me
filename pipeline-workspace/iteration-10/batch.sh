#!/usr/bin/env bash
# Scale tier: the bazaar refunds PR against pr-review before and after the
# iteration-8 subtraction, and the native /code-review. Two samples per arm.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
I8="$IT/../iteration-8"
FX="${FIXTURE_DIR:?set FIXTURE_DIR to a scratch dir outside this repo}"
export RUNS_DIR="$IT/runs"
export FIXTURE_BUILDER="$IT/../../test-repo/bazaar-pr/build_fixture.sh"
for s in a b; do
    "$I8/run_skill.sh" "base-bz-$s"   "$I8/skills-baseline/pr-review/SKILL.md" 1 "$FX" &
    "$I8/run_skill.sh" "cur-bz-$s"    "$IT/../../pr-review/SKILL.md" 1 "$FX" &
    "$I8/run_skill.sh" "native-bz-$s" native:high 1 "$FX" &
done
wait
echo "BATCH COMPLETE"
