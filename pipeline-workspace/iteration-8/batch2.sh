#!/usr/bin/env bash
# Candidate round: the working-tree pr-review on the planted PR (two samples)
# and the clean PR (one sample). Pass a suffix to keep rounds apart (default v2).
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
FX="${FIXTURE_DIR:?set FIXTURE_DIR to a scratch dir outside this repo}"
ARM="${1:-v2}"
CUR="$IT/../../pr-review/SKILL.md"

"$IT/run_skill.sh" "$ARM-pr1-a" "$CUR" 1 "$FX" &
"$IT/run_skill.sh" "$ARM-pr1-b" "$CUR" 1 "$FX" &
"$IT/run_skill.sh" "$ARM-pr2-a" "$CUR" 2 "$FX" &
wait
echo "BATCH2 COMPLETE"
