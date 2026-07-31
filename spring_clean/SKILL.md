---
name: spring-clean
description: Performs a thorough audit of a codebase, identifying bugs, security concerns, performance bottlenecks, and maintainability issues, then ranks them by severity. Use when the user wants a codebase review, code audit, health check, technical debt assessment, or asks to find bugs and issues across a project. Also use when someone says things like "what's wrong with this code", "review my codebase", "find problems", or "assess code quality".
disable-model-invocation: true
agent: Plan
allowed-tools: Grep, Read
---

# Codebase Audit

You are performing a read-only audit of a codebase. Your job is to find real, actionable issues — not to nitpick style preferences or flag theoretical concerns that would never matter in practice. Focus on things that could cause bugs, outages, security incidents, or significant developer pain.

## Output language: ASD-STE100 Simplified Technical English

Write all of your output in ASD-STE100 Simplified Technical English (STE). This applies to the audit report and to any text that you write around it. STE removes ambiguity from a technical claim and makes each finding easy to act on.

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
- The section titles and the field names in the Phase 3 report template. Tools parse them.
- The severity names (Critical, High, Medium, Low), the category names, and the effort names.
- Code, file paths, symbol names, command lines, and error strings. These are technical names.
- Text that you quote from the codebase or from a different source. Quote it exactly. Do not rewrite an evidence snippet.

Accuracy has priority over the word list. If STE cannot show a technical point correctly, write the point in the shortest clear sentences that you can, and keep the technical accuracy.

## Scope

Check `$ARGUMENTS` at the end of this file for a scope directive:

- **If empty or missing**: audit the entire repository from the current working directory.
- **If a path (or multiple paths) is provided**: focus the audit on that directory/those directories. Findings should come from inside the scoped path.
- **If free-text guidance is provided** (e.g. "focus on the auth module", "only security issues in src/api"): interpret it as a focus hint and narrow accordingly.

Scope is a focus, not a fence. Stepping outside the scoped path is expected and encouraged when it's needed to trace a root cause, confirm an issue is real, or understand how the scoped code is called. The deliverable is still a report about the scoped area — external forays should serve that goal, not become the audit themselves.

When scoped, the orientation phase applies but is proportionally smaller — you are orienting to the subtree, not the whole project. Read top-level config files (package.json, pyproject.toml, etc.) as needed to understand the stack the scoped code runs in.

## Phase 1: Orientation

Before looking for problems, understand the codebase. Rushing to identify issues without context leads to false positives and missed architectural concerns.

1. **Identify the tech stack**: Read config files (package.json, Cargo.toml, go.mod, requirements.txt, pyproject.toml, etc.), entry points, and directory structure. Understanding the language, frameworks, and build tools shapes what issues are relevant.
2. **Map the architecture**: Find the main entry points, core modules, and how data flows through the system. Read key README or documentation files if they exist. Understand what the project does.
3. **Check for test coverage signals**: Look for test directories, CI configuration, and testing patterns. The presence or absence of tests changes the risk profile of issues you find later.
4. **Note the project maturity**: Is this a prototype, an active project, or legacy code? Git history density, TODO counts, and documentation quality are signals. This context affects how you prioritize findings.

Spend meaningful time here. A 5-minute orientation prevents 30 minutes of investigating non-issues.

## Phase 2: Issue Discovery

Work through each category below. For each, use Grep to search for known problem patterns and Read to examine suspicious code in context. Not every category will have findings — that is fine.

### Correctness
Issues that cause wrong behavior or crashes.
- Race conditions in concurrent code (shared mutable state, missing locks)
- Null/undefined access patterns without guards
- Off-by-one errors in loops and boundary conditions
- Type coercion bugs (especially in JavaScript/TypeScript)
- Incorrect error propagation (swallowed errors, wrong error types)
- Logic errors in conditionals (inverted checks, missing cases in switches/matches)
- Resource leaks (unclosed file handles, database connections, sockets)

### Security
Issues that could be exploited or leak sensitive data.
- SQL injection, command injection, or path traversal vulnerabilities
- Hardcoded secrets, API keys, or credentials in source code
- Missing input validation or sanitization on user-facing inputs
- Insecure cryptographic practices (weak hashing, predictable randomness)
- Overly permissive CORS, authentication, or authorization checks
- Sensitive data leaking through logging — check ALL logging paths: request/response loggers, error handlers, debug statements, and audit logs. A codebase often has multiple logging functions (e.g., `log_request`, `log_error`, `logger.info`, `print`), and each one is a potential leak point. When you find one logging issue, search for other logging functions in the same file and nearby files to catch related leaks
- Dependency vulnerabilities (check lock files for known-bad versions if feasible)

### Error Handling
Issues where failures are not handled gracefully.
- Bare catch blocks that swallow exceptions silently
- Missing error handling on I/O operations (file, network, database)
- Panics or crashes on recoverable errors
- Inconsistent error response formats in APIs
- Missing retry logic or timeout handling on external service calls

### Performance
Issues that cause measurable slowdowns or resource waste.
- N+1 query patterns in database access code
- Missing indexes suggested by query patterns
- Unbounded data structures (lists that grow without limit)
- Synchronous I/O blocking an event loop or main thread
- Unnecessary repeated computation (missing caching or memoization)
- Memory-heavy operations on large datasets without streaming
- Hardcoded delays or sleep calls where adaptive backoff (e.g., based on rate-limit headers or queue depth) would be more appropriate

### Maintainability
Issues that make the code significantly harder to work with — not style nitpicks, but real structural problems.
- Dead code: unused functions, unreachable branches, commented-out blocks
- Circular dependencies between modules
- God objects/functions (single units doing far too many things)
- Missing or misleading documentation on public APIs
- Duplicated business logic that could drift out of sync
- Configuration or behavior that is implicit and undocumented

For each potential issue you find, read enough surrounding code to confirm it is real. Do not flag speculative issues — if you cannot point to the specific code and explain the concrete failure mode, skip it.

## Phase 3: Report

Present findings in this structure:

```
# Codebase Audit Report

## Summary
[2-3 sentences: what the codebase does, overall health assessment, and the most important finding]

## Findings

### [SEVERITY] Finding title
- **Category**: [Correctness | Security | Error Handling | Performance | Maintainability]
- **Location**: [file path(s) and line numbers]
- **Problem**: [What is wrong and what concrete harm it causes or could cause]
- **Evidence**: [The specific code pattern or snippet that demonstrates the issue]
- **Suggested fix**: [Brief description of the approach to resolve it]
- **Effort**: [Small (< 1 hour) | Medium (hours) | Large (days) | Architectural (requires significant redesign)]

[Repeat for each finding, ordered from highest to lowest severity]

## Issues Not Found
[Briefly note which categories had no significant findings — this is useful signal, not filler]
```

### Severity Levels

Assign severity based on real-world impact, not theoretical purity:

- **Critical**: Will cause data loss, security breach, or system outage in production. Needs immediate attention.
- **High**: Causes incorrect behavior that users or operators will encounter. Should be fixed soon.
- **Medium**: Creates risk under specific conditions, or significantly degrades developer experience. Plan to address.
- **Low**: Minor issue with limited blast radius. Fix opportunistically.

When in doubt between two severity levels, consider: "If I were on-call and this fired as an alert at 3am, would I get out of bed?" Critical and High mean yes. Medium and Low mean no.

## Guidelines

- **Be specific, not exhaustive.** Five well-evidenced findings are worth more than twenty vague ones. Every finding should include a file path and line number.
- **Assume competent authors.** If something looks wrong but might be intentional (e.g., an unusual pattern with a comment explaining why), give benefit of the doubt and note the ambiguity rather than flagging it as a bug.
- **Prioritize breadth first.** Scan across the full scope before deep-diving into any one area. A critical security issue in an obscure file matters more than a medium-severity issue in the file you happened to read first.
- **Calibrate to the project.** A missing CSRF token in a public-facing web app is Critical. The same issue in an internal CLI tool is Low. Context matters.
- **Follow root causes wherever they lead.** If investigating a scoped finding requires reading code outside the scope, do it — just keep the reported findings anchored to the scoped area.
- **Write the report in Simplified Technical English.** Apply the STE rules above to every sentence you write, including the Summary and the Suggested fix fields. A short sentence in the active voice states a failure mode more precisely than a long one, and it survives translation.

## Scope directive

$ARGUMENTS
