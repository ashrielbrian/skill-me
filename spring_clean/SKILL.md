---
name: spring-clean
description: Performs a thorough audit of a codebase, identifying bugs, security concerns, performance bottlenecks, and maintainability issues, then ranks them by severity. Use when the user wants a codebase review, code audit, health check, technical debt assessment, or asks to find bugs and issues across a project. Also use when someone says things like "what's wrong with this code", "review my codebase", "find problems", or "assess code quality".
disable-model-invocation: true
agent: Plan
allowed-tools: Grep, Read
---

# Codebase Audit

You are performing a read-only audit of a codebase. Your job is to find real, actionable issues — not to nitpick style preferences or flag theoretical concerns that would never matter in practice. Focus on things that could cause bugs, outages, security incidents, or significant developer pain.

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
- **Prioritize breadth first.** Scan across the full codebase before deep-diving into any one area. A critical security issue in an obscure file matters more than a medium-severity issue in the file you happened to read first.
- **Calibrate to the project.** A missing CSRF token in a public-facing web app is Critical. The same issue in an internal CLI tool is Low. Context matters.
