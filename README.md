# skill-me

Claude Code skills for codebase auditing and issue validation.

## Skills

### `/spring-clean`

Read-only codebase audit. Scans for bugs, security issues, performance bottlenecks, and maintainability problems. Produces a structured report with findings ranked by severity. Runs with restricted tools (Grep + Read only) — it cannot modify your code.

### `/rebug`

Issue validator. Takes a list of reported issues and independently verifies each one against the actual code. Produces verdicts (Confirmed / Disputed / Reseveritied) with code evidence. Use after `/spring-clean` or with findings from any source.

### `/audit`

Full pipeline in one command. Runs the spring-clean audit, then validates every finding with rebug's methodology — no copy/paste between steps. Trade-off: a single agent validates its own work, so for the strongest independent verification, run `/spring-clean` and `/rebug` as separate steps.

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
