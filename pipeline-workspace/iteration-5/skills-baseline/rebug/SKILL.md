---
name: rebug
description: Critically validates and re-evaluates reported bugs and issues by investigating the actual code. Use when someone has a list of issues (from an audit, code review, static analysis, or bug report) and wants to verify which are real, which are false positives, and whether the proposed fixes are correct. Also use when the user says things like "are these bugs real", "validate this report", "double-check these findings", "second opinion on these issues", or passes output from spring-clean.
agent: Explore
---

# Issue Validation

You have been given a set of reported issues. Your job is to independently verify each one by reading the actual code. Do not trust the report at face value — investigate as if you are seeing this codebase for the first time.

Reports often contain issues that are technically accurate but practically irrelevant, issues where the severity is miscalibrated, issues that are outright wrong because the reporter misread the code, and proposed fixes that would introduce new problems. Your goal is to separate signal from noise.

## Input

Check the `$ARGUMENTS` block at the end of this file:

- **If it looks like a file path** (e.g. `./audit.md`, `/tmp/spring_clean_report.md`, `~/reports/findings.txt`, or any single token ending in a file extension): use the Read tool to load that file, and treat its contents as the report to validate. This is the expected hand-off from `spring-clean`, whose output is often saved to disk.
- **If it looks like inline report text** (multiple lines, markdown headers, findings with file/line references): treat `$ARGUMENTS` itself as the report and work from it directly.
- **If it's empty**: ask the user what they want validated rather than guessing.

When you load a report from a file, briefly note at the top of your output which file you read, so the user can trace the provenance.

## How to Investigate Each Issue

For every reported issue, work through these steps:

### 1. Read the cited code
Go to the file and line numbers referenced in the report. Read enough surrounding context (typically 50-100 lines above and below) to understand the function, module, and data flow. If no specific location is given, search for it yourself.

### 2. Trace the logic
Follow the execution path that the report claims is problematic. Check:
- Does the data actually flow the way the report says it does?
- Are there guards, checks, or error handlers that the report missed?
- Does the framework or runtime provide protections the report didn't account for? (e.g., ORM parameterization preventing SQL injection, framework-level CSRF protection)
- Could the "bug" be intentional behavior? Look for comments, commit messages, or related test cases that explain the design choice.

### 3. Check for existing mitigations
Search for:
- Tests that cover the reported scenario (if tests exist and pass, the issue may be invalid or lower severity than claimed)
- Configuration or middleware that addresses the concern at a different layer
- Type system guarantees that prevent the failure mode
- Documentation or comments that acknowledge the tradeoff

### 4. Evaluate the proposed fix
If the report includes a suggested fix:
- Would the fix actually resolve the issue, or just mask it?
- Could the fix introduce new bugs? (Changing error handling can break callers; adding locks can cause deadlocks; adding validation can reject valid input)
- Is there a simpler or more idiomatic approach for this codebase?
- Does the effort estimate seem right?

### 5. Reach a verdict
Classify the issue as one of:
- **Confirmed**: The issue is real, the severity is appropriate, and it should be fixed.
- **Confirmed, reseveritied**: The issue is real but the severity should be adjusted (state the new severity and why).
- **Partially valid**: The core observation is correct but the details or implications are wrong (explain what's accurate and what isn't).
- **Disputed**: The issue appears incorrect based on your investigation (explain what the report got wrong and what the code actually does).
- **Needs more context**: You cannot determine validity from the code alone — it depends on runtime behavior, deployment configuration, or domain knowledge you don't have (state exactly what information is missing).

## Output Format

```
# Issue Validation Report

## Summary
[How many issues were reviewed. How many confirmed vs disputed. Overall assessment of the original report's accuracy.]

## Detailed Findings

### Issue: [Original issue title]
- **Original severity**: [as reported]
- **Verdict**: [Confirmed | Confirmed, reseveritied | Partially valid | Disputed | Needs more context]
- **Revised severity**: [if changed, otherwise omit]
- **Investigation**: [What you found when you traced the code. Be specific — cite file paths, line numbers, and the actual behavior you observed.]
- **Fix assessment**: [Is the proposed fix correct? If not, what would you recommend instead?]
- **Alternative fix**: [If you have a better approach, describe it here. Omit if the original fix is good.]

[Repeat for each issue]

## New Issues Discovered
[If your investigation uncovered issues the original report missed, list them here using the same format as the original report. This is optional — only include if you actually find something.]
```

## Guidelines

- **Investigate before judging.** Read the code before forming an opinion about whether an issue is valid. Confirmation bias works both ways — don't assume the report is right, and don't assume it's wrong.
- **Show your work.** For each verdict, explain what you found in the code that led to your conclusion. A verdict without evidence is just another unverified claim.
- **Be fair to the original reporter.** If an issue is partially right, say so. Don't dismiss the whole finding because one detail is off.
- **Severity is contextual.** An issue that's Critical in a payment processing system might be Low in a personal blog. Consider the actual deployment context if you can determine it.
- **Don't pad the report.** If every issue checks out, say so concisely. If you find new issues, add them. But don't manufacture disagreements to appear thorough.

## Input argument

$ARGUMENTS
