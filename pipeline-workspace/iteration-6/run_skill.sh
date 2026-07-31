#!/usr/bin/env bash
# Run one skill, one arm, one target, in a separate headless claude process.
# Usage: run_skill.sh <run-id> <skill-md> <arguments> <cwd> <allowed-tools>
set -euo pipefail

IT="$(cd "$(dirname "$0")" && pwd)"
RUN_ID="$1"; SKILL_MD="$2"; ARGS="$3"; WORKDIR="$4"; TOOLS="$5"
OUT="$IT/runs/$RUN_ID"
mkdir -p "$OUT"

python3 "$IT/render_prompt.py" "$SKILL_MD" "$ARGS" "$OUT/prompt.md" >/dev/null

START=$(date +%s)
set +e
(
    cd "$WORKDIR"
    CLAUDE_CONFIG_DIR="$HOME/.claude-bt" claude \
        -p "$(cat "$OUT/prompt.md")" \
        --output-format json \
        --allowedTools "$TOOLS" \
        --disallowedTools "Write Edit NotebookEdit Task Agent" \
        > "$OUT/raw.json" 2> "$OUT/stderr.log"
)
STATUS=$?
set -e
END=$(date +%s)

python3 - "$OUT" "$RUN_ID" "$((END - START))" "$STATUS" <<'PY'
import json, sys
from pathlib import Path
out, run_id, wall, status = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
try:
    data = json.loads((out / "raw.json").read_text())
except Exception as e:
    print(f"{run_id}: FAILED to parse raw.json ({e}) status={status}")
    sys.exit(0)
(out / "report.md").write_text(data.get("result") or "")
u = data.get("usage", {})
timing = {
    "run_id": run_id,
    "wall_seconds": wall,
    "duration_api_ms": data.get("duration_api_ms"),
    "num_turns": data.get("num_turns"),
    "cost_usd": data.get("total_cost_usd"),
    "input_tokens": u.get("input_tokens"),
    "output_tokens": u.get("output_tokens"),
    "cache_read_input_tokens": u.get("cache_read_input_tokens"),
    "cache_creation_input_tokens": u.get("cache_creation_input_tokens"),
    "total_tokens": sum(v or 0 for k, v in u.items() if k.endswith("_tokens")),
    "is_error": data.get("is_error"),
    "permission_denials": len(data.get("permission_denials") or []),
    "exit_status": status,
}
(out / "timing.json").write_text(json.dumps(timing, indent=2))
print(f"{run_id}: {wall}s, {timing['total_tokens']} tok, ${timing['cost_usd']:.2f}, "
      f"{len(data.get('result') or '')} chars, denials={timing['permission_denials']}")
PY
