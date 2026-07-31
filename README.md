# skill-me

Claude Code skills for codebase auditing and issue validation.

## Skills

### `/spring-clean`

Read-only codebase audit. Scans for bugs, security issues, performance bottlenecks, and maintainability problems. Produces a structured report with findings ranked by severity. Runs with restricted tools (Grep + Read only) — it cannot modify your code.

### `/rebug`

Issue validator. Takes a list of reported issues and independently verifies each one against the actual code. Produces verdicts (Confirmed / Disputed / Reseveritied) with code evidence. Use after `/spring-clean` or with findings from any source.

### `/audit`

Full pipeline in one command. Runs the spring-clean audit, then validates every finding with rebug's methodology — no copy/paste between steps. Trade-off: a single agent validates its own work, so for the strongest independent verification, run `/spring-clean` and `/rebug` as separate steps.

### `/address-pr-comments`

Reads the unresolved review comments on a GitHub PR and evaluates each one from first principles by reading the actual code. Produces a balanced verdict (Valid / Invalid / Partially valid / Needs discussion) with evidence and a suggested fix for each valid comment. Use with `/address-pr-comments`, `/address-pr-comments 123`, or `/address-pr-comments <PR URL>`.

### `/pr-review`

Independent first-principles review of a GitHub PR. Reads the diff and PR description, deliberately ignores other reviewers' comments to avoid anchoring, and walks a top-down taxonomy that puts architectural fit, simplicity, and API design ahead of line-level correctness. Sketches the minimal solution to the PR's underlying problem before judging the implementation, so it catches code bloat, duplication, speculative generality, and AI-slop tells alongside correctness issues. Produces a severity-ranked report; optionally creates a temporary git worktree for deeper inspection. Use with `/pr-review`, `/pr-review 123`, or `/pr-review https://github.com/owner/repo/pull/123`.

### `/perform-like-jeff-dean`

Read-only performance audit distilled from Abseil's ["Performance Hints"](https://abseil.io/fast/hints.html). Maps hot and cold paths first, then hunts six categories of waste (algorithms, I/O round trips, allocation, redundant work, data layout, observability overhead). Every finding carries a back-of-envelope estimate anchored in the classic latency-numbers table, and the report is ordered by a priority matrix that weighs impact against effort — the low-hanging, high-impact fruit ranks first, and real-but-cold inefficiencies get a one-line note instead of a finding. Use with `/perform-like-jeff-dean`, or scope it: `/perform-like-jeff-dean src/ingest`.

## Output language

All six skills write their reports in [ASD-STE100 Simplified Technical English](https://www.asd-ste100.org/) — short active sentences, one meaning per word, no idioms or metaphors, and no `-ing` clauses. The intent is precision: a finding stated in 20 words of plain English is harder to misread, harder to wave away, and readable by a team that doesn't share a first language.

Structural elements are exempt and stay verbatim, so downstream tooling keeps parsing: report headings and field names, severity and verdict labels, code snippets, file paths, symbol names, and any text quoted from a diff, a review comment, or the report under validation.

## Prerequisites

- [Claude Code](https://claude.ai/code) installed and initialized (run `claude` at least once so `~/.claude` exists)

## Installation

### 1. Clone this repo

Clone to a permanent location. The installer creates symlinks pointing back here, so the repo must stay in place.

```bash
git clone https://github.com/brian-tang/skill-me.git ~/tools/skill-me
cd ~/tools/skill-me
```

### 2. Run the install script

```bash
chmod +x install.sh
./install.sh
```

This symlinks the skills into `~/.claude/skills/`, making them available in all your projects.

### Updating

```bash
cd ~/tools/skill-me
git pull
```

Symlinks mean updates propagate automatically — no need to re-run the installer.

### Alternative: project-scoped install

Install skills for a single project only:

```bash
./install.sh --project /path/to/your/project
```

### Alternative: copy mode

For environments that don't support symlinks:

```bash
./install.sh --copy
```

Files are copied instead of symlinked. Re-run after `git pull` to get updates.

### Alternative: manual install

```bash
mkdir -p ~/.claude/skills
ln -s /absolute/path/to/skill-me/spring_clean ~/.claude/skills/spring_clean
ln -s /absolute/path/to/skill-me/rebug ~/.claude/skills/rebug
ln -s /absolute/path/to/skill-me/audit ~/.claude/skills/audit
ln -s /absolute/path/to/skill-me/address-pr-comments ~/.claude/skills/address-pr-comments
ln -s /absolute/path/to/skill-me/pr-review ~/.claude/skills/pr-review
ln -s /absolute/path/to/skill-me/perform-like-jeff-dean ~/.claude/skills/perform-like-jeff-dean
```

## Usage

### Full pipeline (recommended)

```
/audit
```

Discovers issues and validates them in one pass. Produces a combined report with validated findings and any disputed false positives.

### Two-step pipeline (stronger verification)

For independent verification where a separate agent validates the findings:

```
/spring-clean
```

Copy the Findings section from the output, then:

```
/rebug <paste findings here>
```

### Individual skills

Use `/spring-clean` alone for a quick audit without validation, or `/rebug` with findings from any source (static analysis, code review, bug reports).

## Uninstall

```bash
./install.sh --uninstall
```

For project-scoped installs:

```bash
./install.sh --uninstall --project /path/to/your/project
```

## Development

See `CLAUDE.md` for the evaluation pipeline, iteration protocol, and metrics used to improve these skills.
