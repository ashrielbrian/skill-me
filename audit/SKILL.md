---
name: audit
description: Full codebase audit pipeline — discovers issues then validates each finding. Use when you want a thorough two-pass review that filters out false positives and calibrates severity. Combines spring-clean and rebug into one command.
disable-model-invocation: true
allowed-tools: Grep, Read
---

# Full Codebase Audit Pipeline

Run a two-phase audit of this codebase. Phase 1 discovers issues; Phase 2 critically validates each one to filter out false positives and calibrate severity.

## Output language: ASD-STE100 Simplified Technical English

Write all of your output in ASD-STE100 Simplified Technical English (STE). This applies to the combined report and to any text that you write around it. STE removes ambiguity from a technical claim and makes each finding easy to act on. The two skill files that you read below state the same requirement — keep it in both phases.

### Words
- Use one word for one meaning. Use the same term for the same thing through the whole report.
- Use short, common words: "use" (not "utilize"), "to" (not "in order to"), "before" (not "prior to"), "because" (not "due to the fact that"), "about" (not "approximately"), "make sure" (not "ensure"), "get" (not "obtain"), "enough" (not "sufficient"), "many" (not "numerous"), "start" (not "commence"), "stop" (not "terminate"), "help" (not "assist"), "let" (not "permit"), "show" (not "demonstrate"), "try" (not "attempt").
- Do not use slang, idioms, or metaphors. Write "the handler does not check the input", not "the handler plays fast and loose with the input".
- Do not put more than three nouns together. Write "the handler for the upload request", not "the upload request handler chain".

### Verbs
- Use the active voice: "the handler ignores the error", not "the error is ignored by the handler".
- Use the imperative in a suggested fix: "Add a timeout to the request."
- Use simple tenses only: simple present, simple past, simple future.
- Do not use an "-ing" form as a noun or to start a clause. Write "The function opens the file and does not close it. This leaks a file handle." Do not write "Opening the file without closing it leaks a file handle."
- Do not remove the verb to make a sentence shorter.

### Sentences and paragraphs
- Keep an instruction to 20 words or fewer. Keep a descriptive sentence to 25 words or fewer.
- Give one instruction per sentence.
- Use the articles "a", "an", and "the" where you can.
- Keep a paragraph to one topic and to six sentences or fewer.
- Use a vertical list when the text has three or more related parts.

### Do not lose information
STE limits the length of a sentence. It does not limit the number of sentences, and it does not limit the facts in the report. If a sentence goes over the limit, split it into two sentences. Never delete a fact to make the text shorter.

- Keep every number, measurement, limit, and default value. A number is technical data. Write "the queue holds 10000 items", not "the queue is large".
- Keep every `file:line` citation. Cite each location that supports the finding, not only the first one.
- Keep the effects that follow from the defect: what else breaks, which caller sees the failure, and what the fix must also change.
- The six-sentence limit applies to one paragraph. If a field needs more facts, write more paragraphs, or use a vertical list.

### What you must not change
STE applies to the prose that you write. Keep these exactly as they are:
- The section titles and the field names in the output template. Tools parse them.
- The severity names (Critical, High, Medium, Low), the category names, the effort names, and the verdict names (Confirmed; Confirmed, reseveritied; Partially valid; Disputed).
- Code, file paths, symbol names, command lines, and error strings. These are technical names.
- Text that you quote from the codebase or from a different source. Quote it exactly. Do not rewrite an evidence snippet.

Accuracy has priority over the word list. If STE cannot show a technical point correctly, write the point in the shortest clear sentences that you can, and keep the technical accuracy.

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
