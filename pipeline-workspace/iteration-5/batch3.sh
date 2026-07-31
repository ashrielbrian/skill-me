#!/usr/bin/env bash
# Batch 3: rebug, chained onto each arm's own spring_clean report.
# Each arm validates the report its own spring_clean produced, so the pipeline is
# measured end to end rather than against a shared input.
set -uo pipefail
IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
SP=/private/tmp/claude-501/-Users-briant-Documents-Programming-skill-me/81b2b2aa-ea01-414f-bcb7-1df8d3566a40/scratchpad
RO="Read Grep Glob"

for arm in ste base; do
    d="$SP/targets/eventbus-rebug-$arm"
    rm -rf "$d"; mkdir -p "$d"
    cp -R "$SP/targets/eventbus/." "$d/"
    cp "$IT/runs/sc-eventbus-$arm/report.md" "$d/audit-report.md"
done

"$IT/run_skill.sh" rebug-eventbus-ste  "$REPO/rebug/SKILL.md" "audit-report.md" "$SP/targets/eventbus-rebug-ste" "$RO" &
"$IT/run_skill.sh" rebug-eventbus-base "$IT/skills-baseline/rebug/SKILL.md" "audit-report.md" "$SP/targets/eventbus-rebug-base" "$RO" &
wait
echo "BATCH3 COMPLETE"
