---
name: audit
description: Full codebase audit pipeline — discovers issues then validates each finding. Use when you want a thorough two-pass review that filters out false positives and calibrates severity. Combines spring-clean and rebug into one command.
disable-model-invocation: true
allowed-tools: Grep, Read
---

# Full Codebase Audit Pipeline

Run a two-phase audit of this codebase. Phase 1 discovers issues; Phase 2 critically validates each one to filter out false positives and calibrate severity.

## Phase 1: Discover Issues

Read the spring-clean audit methodology at `${CLAUDE_SKILL_DIR}/../spring_clean/SKILL.md` and follow it completely:

1. **Orientation** — understand the tech stack, architecture, test coverage, and project maturity
2. **Issue Discovery** — work through all six categories (Correctness, Security, Error Handling, Performance, Maintainability) using Grep and Read
3. **Report** — produce the structured audit report with findings ordered by severity

Save your Phase 1 findings mentally before proceeding. You will need every finding for Phase 2.

## Phase 2: Validate Findings

Now read the rebug validation methodology at `${CLAUDE_SKILL_DIR}/../rebug/SKILL.md` and apply it to every finding from Phase 1.

For each finding you reported:

1. Re-read the cited code with fresh eyes
2. Trace the logic — check for guards, framework protections, or intentional design you may have overlooked
3. Check for existing mitigations (tests, middleware, type system guarantees)
4. Evaluate your own suggested fix — could it introduce new issues?
5. Reach a verdict: Confirmed, Confirmed (reseveritied), Partially valid, or Disputed

Be genuinely critical of your own work. The value of Phase 2 is catching false positives and miscalibrated severity from Phase 1.

## Output

Present a single combined report:

```
# Codebase Audit Report

## Summary
[2-3 sentences: what the codebase does, overall health, most important finding]
[Validation summary: X findings confirmed, Y reseveritied, Z disputed]

## Validated Findings

### [SEVERITY] Finding title
- **Category**: [Correctness | Security | Error Handling | Performance | Maintainability]
- **Location**: [file path(s) and line numbers]
- **Problem**: [What is wrong and the concrete harm it causes]
- **Evidence**: [Specific code pattern demonstrating the issue]
- **Validation**: [What Phase 2 investigation found — confirmed, adjusted, or disputed, with reasoning]
- **Suggested fix**: [Approach to resolve]
- **Effort**: [Small | Medium | Large | Architectural]

[Repeat for each finding, ordered by final validated severity]

## Disputed Findings
[Any Phase 1 findings that Phase 2 determined were false positives, with explanation]

## Issues Not Found
[Categories with no significant findings]
```
