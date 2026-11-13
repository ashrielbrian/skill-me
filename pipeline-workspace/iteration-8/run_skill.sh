#!/usr/bin/env bash
# Run one review arm against the parcelpost PR fixture in a headless claude process.
# Usage: run_skill.sh <run-id> <skill-md | native:<level>> <pr-number> <fixture-dest>
#
# Each run gets its own fixture build, so the gh-shim log and any worktree a run
# leaves behind belong to that run alone. The shim directory goes first on PATH.
set -euo pipefail

IT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$IT/../.." && pwd)"
RUN_ID="$1"; SKILL="$2"; PR="$3"; DEST="$4/$RUN_ID"
OUT="${RUNS_DIR:-$IT/runs}/$RUN_ID"
mkdir -p "$OUT"

"$REPO/test-repo/parcelpost/build_fixture.sh" "$DEST" >/dev/null

if [[ "$SKILL" == native:* ]]; then
    printf '/code-review %s %s' "${SKILL#native:}" "$PR" > "$OUT/prompt.md"
    TOOLS="Read Grep Glob Bash Agent Task Skill"
    DISALLOWED="Write Edit NotebookEdit"
else
    python3 "$IT/render_prompt.py" "$SKILL" "$PR" "$OUT/prompt.md" >/dev/null
    TOOLS="Read Grep Glob Bash"
    DISALLOWED="Write Edit NotebookEdit Task Agent"
fi

START=$(date +%s)
set +e
(
    cd "$DEST/work"
    PATH="$REPO/test-repo/parcelpost/bin:$PATH" CLAUDE_CONFIG_DIR="$HOME/.claude-bt" claude \
        -p "$(cat "$OUT/prompt.md")" \
        --output-format json \
        --allowedTools "$TOOLS" \
        --disallowedTools "$DISALLOWED" \
        > "$OUT/raw.json" 2> "$OUT/stderr.log" < /dev/null
)
STATUS=$?
set -e
END=$(date +%s)

cp "$DEST/work/.git/gh-shim.log" "$OUT/gh_calls.jsonl"
git -C "$DEST/work" worktree list --porcelain | grep -c '^worktree' > "$OUT/worktrees_left.txt" || true
git -C "$DEST/work" branch --format='%(refname:short)' > "$OUT/branches_left.txt"

python3 - "$OUT" "$RUN_ID" "$((END - START))" "$STATUS" <<'PY'
import json, sys
from pathlib import Path
out, run_id, wall, status = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
for stale in ("report.md", "timing.json"):
    (out / stale).unlink(missing_ok=True)
try:
    data = json.loads((out / "raw.json").read_text())
except Exception as e:
    print(f"{run_id}: FAILED to parse raw.json ({e}) status={status}")
    sys.exit(1)
(out / "report.md").write_text(data.get("result") or "")
u = data.get("usage", {})
calls = [json.loads(l) for l in (out / "gh_calls.jsonl").read_text().splitlines() if l.strip()]
timing = {
    "run_id": run_id,
    "wall_seconds": wall,
    "num_turns": data.get("num_turns"),
    "cost_usd": data.get("total_cost_usd"),
    # Top-level usage only. It can leave out subagent tokens, so compare arms on cost_usd.
    "main_loop_tokens": sum(v or 0 for k, v in u.items() if k.endswith("_tokens") and isinstance(v, int)),
    "is_error": data.get("is_error"),
    "permission_denials": len(data.get("permission_denials") or []),
    "exit_status": status,
    "gh_calls": len(calls),
    "gh_review_reads": sum(c["reads_reviews"] for c in calls),
    "gh_mutations": sum(c["mutates"] for c in calls),
    "worktrees_left": int((out / "worktrees_left.txt").read_text().strip() or 1) - 1,
    "branches_left": [b for b in (out / "branches_left.txt").read_text().split() if b != "main"],
}
(out / "timing.json").write_text(json.dumps(timing, indent=2))
print(f"{run_id}: {wall}s, ${timing['cost_usd'] or 0:.2f}, {len(data.get('result') or '')} chars, "
      f"gh={timing['gh_calls']} review_reads={timing['gh_review_reads']} denials={timing['permission_denials']}")
PY
