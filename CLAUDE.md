# Skill Evaluation & Iteration Pipeline

This repo contains two complementary skills — `spring_clean` (codebase auditor) and `rebug` (issue validator) — and the evaluation infrastructure for iteratively improving them via the ralph-wiggum method.

## Skills

### spring_clean
A read-only codebase audit skill. Runs with `agent: Plan`, restricted to `Grep` and `Read` tools. Produces a structured report with findings categorized by severity (Critical/High/Medium/Low) and type (Correctness/Security/Error Handling/Performance/Maintainability). User-invoked only (`disable-model-invocation: true`).

**Skill file**: `spring_clean/SKILL.md`

### rebug
An issue validation skill. Runs with `agent: Explore`. Takes a list of reported issues (via `$ARGUMENTS`) and independently verifies each by reading the actual code. Produces verdicts (Confirmed / Confirmed, reseveritied / Partially valid / Disputed / Needs more context) with code evidence.

**Skill file**: `rebug/SKILL.md`

### Pipeline
These skills compose as a pipeline: `spring_clean` audits a codebase and produces findings, then `rebug` validates those findings. The output format of `spring_clean` feeds directly into `rebug` as `$ARGUMENTS`.

## Version Control

**Git is the source of truth for skill versions.** Every iteration that objectively improves on the previous version gets committed. Evaluation results are tied to the commit hash of the skill version they tested.

### Commit protocol
1. Run evaluation pipeline (see below)
2. Compare metrics against the previous iteration
3. If the new version is objectively better (higher recall, precision, or pass rate without meaningful regressions), commit the skill changes:
   ```bash
   git add spring_clean/SKILL.md rebug/SKILL.md
   git commit -m "skill(spring_clean,rebug): <what changed>

   Iteration N results vs previous (commit <short-hash>):
   - spring_clean recall: X% -> Y%
   - spring_clean precision: X% -> Y%
   - pipeline pass_rate: X% -> Y%
   - tokens: Xk -> Yk
   "
   ```
4. If the new version regresses, do NOT commit. Revert changes and try a different approach.

### Baseline for comparison
The baseline is always the previous git commit's version of the skill files. To run a baseline:
```bash
# Get the skill at the previous commit
git show HEAD:spring_clean/SKILL.md > /tmp/baseline_spring_clean.md
git show HEAD:rebug/SKILL.md > /tmp/baseline_rebug.md
```

## Directory Structure

```
skill_me/
├── CLAUDE.md                          # This file
├── spring_clean/
│   ├── SKILL.md                       # Current skill (edit this)
│   └── evals/evals.json               # Eval prompts and assertions
├── rebug/
│   ├── SKILL.md                       # Current skill (edit this)
│   └── evals/evals.json               # Eval prompts and assertions
├── pipeline-workspace/
│   └── iteration-N/                   # Results for iteration N
│       ├── manifest.json              # Commit hash, targets, timestamp
│       ├── benchmark.json             # Aggregated metrics
│       ├── ground_truth.json          # Planted issues (if synthetic target)
│       └── <eval-name>/
│           ├── eval_metadata.json     # Prompt and assertions
│           ├── current/               # Current skill version output
│           │   ├── outputs/           # spring_clean_report.md, report.md
│           │   ├── timing.json
│           │   └── grading.json
│           └── baseline/              # Previous commit skill version output
│               ├── outputs/
│               ├── timing.json
│               └── grading.json
├── test-repo/
│   └── taskflow/                      # Synthetic project with 28+ planted bugs
└── history.json                       # Cross-iteration metrics log
```

## Evaluation Methodology

### Pipeline Eval
Run the full `spring_clean -> rebug` pipeline, comparing current skills against the baseline (previous commit).

For each pipeline eval:
1. **Extract baseline skills** from the previous commit via `git show HEAD:<path>`
2. **Run current spring_clean** on target -> save output -> chain to **current rebug** -> save output
3. **Run baseline spring_clean** on target -> save output -> chain to **baseline rebug** -> save output
4. **Grade** both pipelines against shared assertions -> `grading.json`
5. **Score against ground truth** (if synthetic target) -> recall, precision
6. **Aggregate** into `benchmark.json`
7. **Launch eval viewer** for human review
8. **Write manifest.json** tying results to skill commit hash

### manifest.json
Every iteration directory must contain a `manifest.json`:
```json
{
  "iteration": 3,
  "timestamp": "2026-03-30T12:00:00Z",
  "current_skill_commit": "abc1234",
  "baseline_skill_commit": "def5678",
  "targets": ["~/Documents/graph-dl", "test-repo/taskflow"],
  "has_ground_truth": true,
  "skill_changes_summary": "Added concrete grep patterns for SQL injection detection"
}
```

### Ground Truth Eval (Synthetic Repo)
Use `test-repo/taskflow/` — a purpose-built project with 28+ known planted issues — to measure precision and recall.

- `ground_truth.json` contains the manifest of all planted issues with IDs, severity, category, file, line, and description
- Grade spring_clean output by matching findings to ground truth IDs
- **Recall** = planted issues found / total planted issues
- **Precision** = real issues reported / total issues reported
- Rebug should confirm planted issues and dispute any false positives

## Metrics Tracked Per Iteration

| Metric | Source | What it measures |
|--------|--------|-----------------|
| `assertion_pass_rate` | grading.json | Structural quality (template, required fields) |
| `recall` | ground_truth match | % of real issues found |
| `precision` | ground_truth match | % of reported issues that are real |
| `false_positive_count` | grading | Issues reported that don't exist |
| `severity_accuracy` | ground_truth match | Whether severity ratings match expected |
| `total_tokens` | timing.json | Cost efficiency |
| `duration_seconds` | timing.json | Wall-clock time |
| `rebug_confirmation_rate` | rebug grading | % of findings rebug confirms |
| `rebug_new_issues_found` | rebug output | Issues rebug finds that spring_clean missed |

## history.json

Append after each graded iteration. This is the longitudinal record across all commits:

```json
{
  "iterations": [
    {
      "id": 1,
      "skill_commit": "a8432cf",
      "baseline_commit": null,
      "timestamp": "2026-03-27T03:30:00Z",
      "targets": ["~/Documents/graph-dl"],
      "changes": "Initial rewrite: added phases, taxonomy, output template, guidelines",
      "metrics": {
        "pipeline_pass_rate": {"current": 1.0, "baseline": 0.86},
        "tokens": {"current": 214017, "baseline": 216463},
        "time_s": {"current": 477, "baseline": 651}
      },
      "verdict": "committed"
    }
  ]
}
```

## Ralph-Wiggum Iteration Protocol

### 1. Run the eval
Run the pipeline on both `~/Documents/graph-dl` (real-world) and `test-repo/taskflow` (ground truth). This gives both qualitative signal (does the output look good?) and quantitative signal (did it find the planted issues?).

### 2. Identify the weakest assertion
Look at `benchmark.json` across both targets. Find the assertion or metric that performs worst. This is the improvement target for this iteration.

### 3. Read the transcripts
Before editing the skill, read the subagent transcripts (`.output` files) to understand *why* the skill failed. Common failure patterns:
- **Missed issue**: skill didn't instruct the model to look for this category
- **False positive**: skill was too aggressive, or model hallucinated a finding
- **Poor structure**: model followed instructions but output was hard to parse
- **Wasted effort**: model spent tokens on unproductive exploration

### 4. Make a targeted edit
Edit SKILL.md to address the specific failure. Prefer explaining *why* over adding rigid rules. If the model missed SQL injection, don't add "ALWAYS check for SQL injection" -- instead, add a concrete pattern to search for under the Security category.

### 5. Re-run and compare
Run the pipeline again into the next iteration directory. Compare against baseline (previous commit):
- Did the targeted metric improve?
- Did any other metric regress?

### 6. Commit or revert
- **Objectively better**: commit the changes with metrics in the commit message. Update `history.json`.
- **Regression**: do NOT commit. Revert changes, try a different approach.
- **Mixed results**: use judgment. If recall improved but tokens increased modestly, that may be acceptable. If precision dropped, probably not.

### 7. Expand the test surface periodically
Create new synthetic repos or test against new real repos to avoid overfitting. Good targets:
- Different languages or frameworks (not just Python/FastAPI)
- Subtle bugs (not just obvious injection)
- Clean code mixed with bugs (tests false positive rate)
- Different project sizes

## Regression Detection

After each iteration, compare metrics against the previous iteration's `benchmark.json`:

- **Regression**: metric worsened by >5%
- **Improvement**: metric improved by >5%
- **Stable**: within 5%

Key regressions to block commits:
- Recall dropping (skill misses real issues it used to find)
- Precision dropping significantly (more false positives)
- Rebug confirmation rate dropping (spring_clean quality degraded)

Acceptable tradeoffs:
- Modest token increase (<20%) if recall or precision improved meaningfully
- Modest time increase if output quality improved

## Operational Notes

- Subagents frequently hit Write permission issues. Extract reports from `.output` transcript files using the JSON extraction pattern (parse JSONL, find Write tool calls, extract content field).
- The eval viewer is at: `~/.claude/plugins/marketplaces/claude-plugins-official/plugins/skill-creator/skills/skill-creator/eval-viewer/generate_review.py`. Use `--static /tmp/review.html` for CLI mode.
- When running pipeline evals, launch both spring_clean runs in parallel, then chain rebug after each completes.
- To get baseline skill files without a snapshot directory: `git show <commit>:spring_clean/SKILL.md`
