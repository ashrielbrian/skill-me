#!/usr/bin/env bash
# Baseline round: pr-review at HEAD and the native /code-review, on the planted
# PR (#1, two samples) and the clean PR (#2, one sample).
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
FX="${FIXTURE_DIR:?set FIXTURE_DIR to a scratch dir outside this repo}"
BASE="$IT/skills-baseline/pr-review/SKILL.md"

"$IT/run_skill.sh" base-pr1-a   "$BASE" 1 "$FX" &
"$IT/run_skill.sh" base-pr1-b   "$BASE" 1 "$FX" &
"$IT/run_skill.sh" base-pr2-a   "$BASE" 2 "$FX" &
"$IT/run_skill.sh" native-pr1-a native:high 1 "$FX" &
"$IT/run_skill.sh" native-pr1-b native:high 1 "$FX" &
"$IT/run_skill.sh" native-pr2-a native:high 2 "$FX" &
wait
echo "BATCH1 COMPLETE"
