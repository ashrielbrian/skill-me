---
name: pr-review
description: Independently reviews a GitHub pull request from first principles — reads the diff and PR description, deliberately ignores other reviewers' comments to avoid anchoring, and produces a severity-ranked report covering architectural fit, API design, correctness, security, performance, testing, observability, and maintainability. Optionally creates a temporary git worktree for deeper inspection. Use when the user says things like "review this PR", "give me an independent review of PR #123", "code review this PR", or passes a PR URL/number for a fresh look.
agent: Plan
allowed-tools: Bash, Read, Grep
---

# Independent PR Review

You have been asked to review a GitHub pull request **independently and from first principles**. Your job is to evaluate the change on its own merits by reading the code, not to absorb other reviewers' opinions and rephrase them. Reviewers who have already commented may be right, partially right, or wrong — but you are not their proxy. You read the code; you decide.

Two principles drive everything below:

1. **Independence.** Do not fetch, read, or even glance at existing review threads, review comments, or prior reviewer activity on the PR. Reading them anchors you. The PR description (the author's claim) is allowed, but it is treated as a claim to verify, not as endorsement.
2. **Design before implementation.** A correct implementation of the wrong design is often worse than a buggy implementation of the right design, because design mistakes calcify. Walk the taxonomy top-down — architecture and API design come *before* line-level correctness, performance, and style.

The GitHub CLI (`gh`) is installed and authenticated. Use it to fetch PR data; use Read and Grep to investigate the code.

## Input

Check the `$ARGUMENTS` block at the end of this file:

- **If empty or missing**: auto-detect the PR for the current branch. Run `gh pr view --json number,url,title,headRepository,headRepositoryOwner,baseRepository` — if there is no PR for the current branch, say so and ask the user which PR they meant.
- **If it's a PR number** (e.g. `123`, `#123`): use that number against the current repo. Resolve owner/repo with `gh repo view --json owner,name`.
- **If it's a PR URL** (e.g. `https://github.com/owner/repo/pull/123`): parse the owner, repo, and PR number from the URL.
- **If it's a free-text focus hint** (e.g. "focus on the auth changes", "skim the tests, deep-dive the migration"): first auto-detect the PR as above, then weight your effort accordingly. The focus is a hint, not a fence — if a change outside the focused area is clearly broken, still report it.

Briefly note at the top of your output which PR you are reviewing (owner/repo#number and title), so the user can trace the provenance.

## Phase 1: Fetch PR metadata and diff (NOT comments)

Run **only these** `gh` calls:

```bash
gh pr view <NUMBER> --json number,title,url,body,headRefName,headRefOid,baseRefName,additions,deletions,changedFiles,files,author,isDraft,mergeable
gh pr diff <NUMBER>
```

**Do not** request the `reviews`, `comments`, `reviewThreads`, `latestReviews`, or `reviewRequests` fields. **Do not** run the GraphQL `reviewThreads` query that `address-pr-comments` uses. Avoiding those fields is the independence mechanism — once you've read another reviewer's framing, you cannot un-read it.

Record the head SHA (`headRefOid`); every finding's location should be cited at that SHA, so the report stays meaningful after rebases.

## Phase 2: Orientation

Before judging the change, build the context to judge it from.

1. **Read the diff yourself first.** From the unified diff, write a neutral 2-3 sentence description of what the change actually does. This goes into the "What this PR does (from the diff)" section of the report. Doing this *before* reading the PR body is the anti-anchor step against the author's framing.
2. **Then read the PR description (`body`).** Treat it as a claim about what the change does and why. Note any specific assertions ("fixes the race in X", "no behavior change") that you will need to verify against the diff in Phase 4.
3. **Map the change shape.** From `files`, `additions`, `deletions`: is this one tight change in one file, or cross-cutting? New feature, refactor, bugfix, dependency bump, infra change? The shape changes which categories matter most.
4. **Read the surrounding code, not just the diff hunks.** For every changed file, Read the full file (or at least the surrounding function, class, and immediate callers). The diff hides invariants, callers, types, and — crucially — the patterns the codebase already uses for this kind of work. **This is the single most important step for architectural-fit findings.** Skipping it is how reviewers miss design issues and over-focus on line-level nits.
5. **Find the precedents.** Before judging any non-trivial new code, Grep for sibling/analogous code already in the repo: other route handlers, other migrations, other components of the same kind, other places the same problem has been solved. Note 1-2 concrete file:line references for each pattern you find — you will cite these in any architectural-fit finding.

Spend real time here. The orientation determines whether you can see design issues at all.

## Phase 3 (optional): Temporary worktree for deeper inspection

For most PRs, reading the diff plus the files at the repo's current HEAD is enough. Escalate to a worktree only when one of these is true:

- The PR adds new files you cannot otherwise Read (they don't exist at HEAD).
- The change touches many files where cross-file reasoning at the PR's head state matters and HEAD has diverged.
- The user's current branch is unrelated to the PR, so reading HEAD would mislead you about what the PR sees.

If you decide to use a worktree, follow this pattern exactly — and clean up when done:

```bash
# 1. Fetch the PR's head ref into a local branch (does NOT touch the current checkout)
git fetch origin pull/<NUMBER>/head:pr-<NUMBER>-review

# 2. Create a worktree in a temp dir
WT="$(mktemp -d)/pr-<NUMBER>"
git worktree add "$WT" pr-<NUMBER>-review

# 3. Investigate inside $WT with Read / Grep
#    (do NOT cd into the user's main checkout to make edits — there are none to make)

# 4. Clean up when done — both the worktree and the local branch you created
git worktree remove "$WT"
git branch -D pr-<NUMBER>-review
```

Never `gh pr checkout` into the user's working tree — that mutates their checkout. Never leave a worktree behind. If you created a worktree, removing it is part of the task, not a nicety.

## Phase 4: First-principles evaluation

Walk the taxonomy below **in order, top-down**. Design problems are higher leverage than implementation problems, and listing them last buries them under a pile of small findings. For each meaningful change (group related hunks together; don't review the diff line-by-line):

1. State what the change does in neutral terms — *before* judging it.
2. Compare against the surrounding code: callers, types, framework guarantees. Does it preserve invariants? Introduce new failure modes?
3. Walk the categories below. Not every category will have findings — that's fine and worth saying so explicitly in the report.
4. Confirm each candidate finding with `file:line` at the PR's head SHA before recording it. No speculative findings.

### Architectural fit
The change is correct in isolation; the question is whether it belongs *here*, *like this*, given what the rest of the codebase already does.

- **Pattern match.** Does the change look like other implementations of similar features in this codebase? Compare structure, naming, layering, error-handling style, dependency direction. **For every architectural-fit finding, cite a concrete sibling with file:line so the author can see the pattern the change diverges from.** "This doesn't match the codebase's style" without a precedent is opinion, not a finding.
- **Right layer.** Business logic leaking into a controller; DB access in a view; cross-cutting concerns duplicated inline rather than extracted; a feature that should be a middleware implemented as a helper called from twelve places.
- **Reused utilities.** Did the author build a helper, parser, error type, or constant that already exists somewhere in the repo? Grep before judging the new code on its own merit. Duplicated logic that will drift is an architectural finding, not a maintainability nit.
- **Right time to abstract.** Premature generalization (a flexible framework for a one-shot need) and missed abstraction (the third copy of the same five lines) are both findings.
- **Module boundaries / coupling.** Does the change create a new dependency edge that crosses a boundary the rest of the codebase respects (e.g. `api` importing from `internal`, a model reaching into a sibling domain)?

### API & interface design
Anything that becomes a contract: exported functions, types, route shapes, CLI flags, event payloads, public config.

- Naming, ergonomics, and consistency with sibling APIs.
- Backward compatibility — breaking changes to callers, DB schemas, on-the-wire formats, public types. If breaking, is there a migration path?
- Defaults: safe, least-surprising, and consistent with the rest of the API surface.

### Best practices & idioms
- Language idioms appropriate to the stack (Pythonic, Go-style, Rust ownership patterns, modern JS/TS, framework-specific conventions).
- Framework conventions (Django ORM patterns, React hook rules, ASGI middleware ordering, etc. — adjust to the stack you identified in orientation).
- Concurrency and async correctness: proper `await`, no blocking calls on the event loop, lock ordering, cancellation handling.

### Correctness
- Race conditions in concurrent code (shared mutable state, missing locks).
- Null/undefined access without guards.
- Off-by-one errors in loops and boundaries.
- Type coercion bugs.
- Incorrect error propagation (swallowed errors, wrong error types).
- Logic errors in conditionals (inverted checks, missing cases in switches/matches).
- Resource leaks (unclosed file handles, DB connections, sockets).

### Security
- SQL injection, command injection, path traversal.
- Hardcoded secrets, API keys, credentials.
- Missing input validation/sanitization on user-facing inputs.
- Insecure crypto (weak hashing, predictable randomness).
- Overly permissive CORS, authn, or authz checks.
- Sensitive data leaking through logging — check ALL logging paths in the changed files and their neighbors.
- **New attack surface introduced by the PR**: a new endpoint, file upload, deserialization path, eval-style construct, or third-party dependency. Flag explicitly even if nothing in the new surface is obviously broken — the surface itself is the finding.

### Error handling
- Bare catch blocks that swallow exceptions silently.
- Missing error handling on I/O (file, network, database).
- Panics or crashes on recoverable errors.
- Inconsistent API error response formats.
- Missing retry logic or timeouts on external service calls.

### Performance
- N+1 query patterns.
- Missing indexes suggested by new query patterns.
- Unbounded data structures.
- Synchronous I/O blocking an event loop or main thread.
- Missing caching/memoization where appropriate.
- Memory-heavy operations on large datasets without streaming.
- Hardcoded sleeps where adaptive backoff would be more appropriate.

### Testing
- Does the PR add tests for the new behavior? Are they meaningful — do they assert behavior, or just that no exception was raised?
- Are edge cases covered (empty input, boundary values, failure paths)? Are test names descriptive?
- For bug fixes: is there a regression test that would have failed without the fix?
- For new public APIs: is there at least one test exercising the contract?

### Observability
- Logging at appropriate levels; sensitive data redacted; new error paths surfaced.
- Metrics/traces for new code paths if the codebase's conventions call for it.
- Sufficient context in error messages and logs to diagnose failures in production.

### Maintainability
- Dead code: unused functions, unreachable branches, commented-out blocks left behind by the change.
- Circular dependencies introduced by the change.
- God objects/functions (a single unit doing far too many things).
- Missing or misleading documentation on new public APIs.
- Duplicated business logic that could drift out of sync.
- Implicit, undocumented behavior or configuration.

### Scope & PR hygiene
- Is the PR doing one thing, or bundling unrelated changes that should be split?
- Drive-by edits (whole-file reformatting, tangential renames) that inflate review burden and risk.
- Reversibility: can this change be rolled back cleanly, or does it commit to migrations/data shape changes that are hard to undo? Hard-to-reverse changes deserve a higher severity bar.

### Intent vs implementation
Cross-check the PR description against the diff. Flag mismatches:

- **Over-claim**: "fixes the X race" but the diff only narrows the window without fixing the underlying race.
- **Under-claim**: description says "small refactor" but the diff also silently changes user-visible behavior.
- **Mis-claim**: description describes a fix to X, but the diff actually changes Y.

These belong in their own finding category — they're how a PR ships the wrong thing past a busy reviewer.

## Phase 5: Output report

```
# PR Independent Review

## PR
[owner/repo#number — title]
[PR URL]
[N files changed, +X/-Y lines, head <short-sha>]

## What this PR does (from the diff)
[Your own 2-3 sentence neutral read of the change, written before reading the description]

## What the author says it does
[1-2 sentences paraphrased from the PR body — or "(no description provided)" if the body is empty]

## Summary
[2-3 sentences: overall assessment, does the implementation match the intent, what is the single most important finding. If nothing significant was found, say so directly.]

## Findings

### [SEVERITY] Finding title
- **Category**: Architectural fit | API & interface design | Best practices & idioms | Correctness | Security | Error handling | Performance | Testing | Observability | Maintainability | Scope & PR hygiene | Intent vs implementation
- **Location**: path:line (head <short-sha>)   *(or "PR-wide" for cross-cutting design findings)*
- **Problem**: [What is wrong and what concrete harm it causes or could cause]
- **Evidence**: [The specific code pattern or snippet. For architectural-fit findings, cite the sibling/analogous code with file:line so the author can see the pattern the change diverges from.]
- **Suggested fix**: [Brief description of the approach]
- **Effort**: Small (< 1 hour) | Medium (hours) | Large (days) | Architectural (significant redesign)

[Order: highest severity first. Within the same severity, design findings (Architectural fit, API design, Scope) before implementation findings.]

## Issues Not Found
[Briefly note which categories had no significant findings — useful signal, not filler]

## Open Questions
[Things you can't determine from the code alone — depend on intent, runtime configuration, domain context, deployment topology. State exactly what information would resolve each one.]
```

### Severity levels

Assign severity by real-world impact, not theoretical purity:

- **Critical** — Will cause data loss, security breach, or system outage in production. Or: locks in an architectural mistake that will be very expensive to unwind. Needs immediate attention.
- **High** — Causes incorrect behavior users or operators will encounter, or introduces a design choice that will hurt maintainability for a long time. Should be fixed before merge.
- **Medium** — Creates risk under specific conditions, or significantly degrades developer experience. Plan to address.
- **Low** — Minor issue with limited blast radius. Fix opportunistically.

Two calibration tests:

- **The 3am pager test** (for runtime/security/correctness findings): "If I were on-call and this fired as an alert at 3am, would I get out of bed?" Critical and High mean yes.
- **The one-year regret test** (for architectural/design findings): "If this lands and we have to live with it for a year, will we regret it?" Yes → at least High. The code may not be broken today, but a load-bearing design mistake compounds.

## Guidelines

- **Do not fetch existing reviews or review comments.** The `gh` flags listed in Phase 1 are the complete allowed set. Independence is the whole point of this skill.
- **Design before implementation.** Walk the taxonomy in the order written. Don't bury an architectural finding under twenty line-level nits.
- **Every architectural-fit finding needs a concrete precedent.** Before claiming a change doesn't fit the codebase's patterns, Grep for at least one sibling/analogous implementation and cite it with `file:line`. Pattern-fit claims without a cited sibling are opinion.
- **Look for the utility the author missed.** When the PR introduces a helper, parser, error type, or constant, search the repo for an existing equivalent before reviewing the new code in isolation. Duplicated logic that will drift is a real finding.
- **Read the diff first, then the description.** Writing "What this PR does (from the diff)" before "What the author says it does" is the anti-anchor step against the author's framing — the same mechanism, in reverse, that keeps you independent from other reviewers.
- **Don't auto-trust the description.** Treat it as a claim. Verify against the diff.
- **Read full files, not just diff hunks.** The diff hides invariants, callers, and the patterns the change should be fitting into. This is where design findings come from.
- **Cite head SHA + line.** Line numbers from the head ref, not local HEAD, so the report stays meaningful after rebases.
- **Calibrate severity to blast radius.** A wrong design choice that downstream code will depend on is High/Critical even if no bug is visible today. A correctness bug in a rarely-hit branch may be Medium.
- **Be specific, not exhaustive.** Five well-evidenced findings are worth more than twenty vague ones. Every finding has a file path and line number (or "PR-wide" for cross-cutting design findings, with the sibling citations doing the locating).
- **Assume competent authors.** If something looks wrong but might be intentional, give benefit of the doubt and surface the ambiguity in Open Questions rather than firing a finding.
- **No style nits.** Naming, formatting, and aesthetic preferences are not findings unless they hurt clarity or break a clearly established codebase convention.
- **Worktree is opt-in, not default.** Only escalate when Phase 3's criteria are met. If you create one, you remove it before finishing.
- **Don't apply edits or post comments on the PR.** This skill produces a report and stops. The user decides what to do with it.

## Input

$ARGUMENTS
