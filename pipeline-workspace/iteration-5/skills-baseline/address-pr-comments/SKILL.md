---
name: address-pr-comments
description: Reads unaddressed (unresolved) review comments on a GitHub pull request — or PR-comment-style feedback in a local file — and evaluates each one from first principles by reading the actual code, producing a balanced verdict (Valid / Invalid / Partially valid / Needs discussion) with evidence and a suggested fix for valid comments. Use when the user says things like "go through my PR comments", "what should I do about these review comments", "address the feedback on PR #123", "check the file foo.md for the PR comments", or passes a PR URL/number/file path for review.
agent: Explore
allowed-tools: Bash, Read, Grep
---

# PR Comment Review

You have been asked to work through review comments on a pull request. Source can be either a GitHub PR (you fetch the unresolved threads via the API) or a local file the user points you at (you extract comments from it). Your job is the same either way: evaluate each comment independently by reading the actual code — not to defer to the reviewer, and not to dismiss them. Treat each comment as a claim about the code that you need to verify yourself.

The GitHub CLI (`gh`) is installed and authenticated. Use it to fetch PR data when the source is GitHub; use `Read` and `Grep` to investigate the code regardless of source.

## Input

Check the `$ARGUMENTS` block at the end of this file:

- **If empty or missing**: auto-detect the PR for the current branch. Run `gh pr view --json number,url,title,headRepository,headRepositoryOwner,baseRepository` — if there is no PR for the current branch, say so and ask the user which PR they meant.
- **If it's a PR number** (e.g. `123`, `#123`): use that number against the current repo. Resolve owner/repo with `gh repo view --json owner,name`.
- **If it's a PR URL** (e.g. `https://github.com/owner/repo/pull/123`): parse the owner, repo, and PR number from the URL.
- **If it's a file path or a phrase pointing to a file** (e.g. `notes/pr-342-comments.md`, `independent-pr-review-342.md`, "check the file foo.md for the pr comments", "use the comments in `bar.txt`"): treat the file as the source of comments instead of GitHub. Read the file with `Read` and follow Phase 1B below. If the path doesn't exist, say so and ask the user where the file is.
- **If it's a free-text focus hint** (e.g. "only alice's comments", "skip the nits", "focus on the auth thread"): first auto-detect the PR as above, then narrow the threads you evaluate according to the hint. If the hint also mentions a file, file mode wins — use the file as the source.

Briefly note at the top of your output which source you are reviewing (owner/repo#number and title for GitHub mode; file path for file mode), so the user can trace the provenance.

## Phase 1: Gather the comments to evaluate

Phase 1 has two paths depending on input. Pick exactly one. Both produce the same shape — a list of comment threads with reviewer, comment body, and (where available) a file:line citation — which Phase 2 then consumes.

### Phase 1A — From GitHub (PR number / URL / auto-detected PR)

Use the GraphQL API — REST does not expose thread resolution state. Run:

```bash
gh api graphql -F owner=<OWNER> -F repo=<REPO> -F pr=<NUMBER> -f query='
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      url
      title
      headRefOid
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          originalLine
          comments(first: 100) {
            nodes {
              author { login }
              body
              createdAt
              url
              diffHunk
            }
          }
        }
      }
    }
  }
}'
```

Filter the result to threads where `isResolved == false`. Keep `isOutdated == true` threads in scope but flag them — many will still represent live concerns, but some are stale because the code moved; the user benefits from knowing which is which.

If a thread has multiple comments, the chronological order matters: the first comment is the original concern, later comments may include reviewer follow-ups, author replies, or concessions. Read the whole thread before evaluating.

### Phase 1B — From a file

Read the file with `Read`. The file's format is not fixed — it might be a paste of GitHub review threads, a markdown summary, the output of `/pr-review` (a sibling skill in this repo) or a similar tool, or freeform notes. Your job is to identify the **distinct review comments inside it** and normalize them into the same shape Phase 2 consumes.

**Common composition: `/pr-review` output as input.** A frequent use case is feeding a saved `/pr-review` report into this skill — the user runs `/pr-review` on a PR, saves the markdown to a file, then runs `/address-pr-comments <that-file>` to verdict each finding. If the file looks like a `/pr-review` report (has a `# PR Independent Review` heading, a `## Findings` section, entries with `### [SEVERITY]`, `**Category**`, `**Location**`, `**Problem**`, `**Evidence**`, `**Suggested fix**`), treat each finding under `## Findings` as one review comment:

- **Reviewer** → "/pr-review" (the source skill, not a human handle)
- **Comment body** → the finding's `Problem` + `Evidence` + `Suggested fix` fields, kept together so Phase 2 can verify the full claim
- **Location** → the finding's `Location` field (`path:line` at the head SHA noted in the report header)
- **Diff hunk / snippet** → whatever the `Evidence` field shows

Ignore `## Summary`, `## What this PR does`, `## What the author says it does`, `## Issues Not Found`, and `## Open Questions` — those aren't claims to verdict; the Findings are.

For each comment you extract, pull out (where present):

- **Reviewer**: name/handle of who wrote it, or "unknown" if not stated.
- **Comment body**: the actual claim, in the reviewer's voice. Preserve enough wording that you can later quote it.
- **Location**: `path:line` if cited, or the looser anchor the file gives (e.g. function name, hunk excerpt). If the file gives no location, record "unspecified" — you'll have to find the relevant code yourself in Phase 2.
- **Diff hunk / snippet**: if the file shows the cited code, capture it — it's the equivalent of GraphQL's `diffHunk` and helps relocate the code if line numbers don't match `HEAD`.
- **Thread context**: if a single comment has multiple turns (reviewer follow-up, author reply), keep them grouped together.

The file does **not** carry GitHub's resolution state, so treat every extracted comment as in-scope unless the file itself marks something as resolved/dismissed.

Be conservative about what counts as a "comment". Section headers, the file's own summary prose, and meta-commentary about the review are not comments to evaluate — only items that make a claim about the code. If the file contains zero recognizable review comments, stop and tell the user so they can point you at the right input.

Briefly list the extracted comments back to the user (one line each: reviewer + short summary + location) before proceeding to Phase 2, so they can spot anything you missed or wrongly included.

## Phase 2: Evaluate each thread from first principles

For every unresolved thread, work through these steps **in order**. The order matters — reading the code before fully absorbing the reviewer's framing reduces anchoring bias.

### 1. Read the cited code first
Go to `path:line` and read enough surrounding context (typically 50-100 lines, or the whole function/class) to understand what the code does. If `isOutdated == true`, the line numbers may not match current `HEAD` — search for the relevant code by symbol or by content from the `diffHunk`.

If location is **unspecified** (only happens in Phase 1B file mode, when the source file didn't include a citation): use the comment body and any embedded snippet to locate the code yourself with `Grep`. If you can't find it after a reasonable search, mark the thread **Needs discussion** with "code referenced by the comment could not be located" as the missing information — don't guess.

### 2. State what the code actually does
In your own words, describe the behavior of the cited code in neutral terms — what it computes, what inputs it takes, what edge cases it handles, what guarantees it relies on. Do this **before** you weigh the reviewer's claim. This is the anti-anchor step; do not skip it.

### 3. Read the reviewer's claim
Now read the comment body. Identify the specific claim being made: is the reviewer asserting a bug, suggesting a refactor, asking a question, or flagging a style preference? Reduce the comment to its core assertion in one or two sentences.

### 4. Verify the claim against the code
Check whether the claim matches the code's actual behavior:
- Does the data flow, edge case, or failure mode the reviewer describes actually exist?
- Are there guards, type constraints, framework protections, or tests that the reviewer may have missed?
- If the reviewer cites a specific input or scenario, trace it through the code yourself.
- If the reviewer proposes a fix, would the fix actually resolve the concern, or introduce new problems?

### 5. Factor in the rest of the thread
If there are follow-up comments — from the reviewer, the author, or others — read them and weigh them. Has someone already conceded a point? Has the author given context the reviewer hadn't seen? Don't defer to any side; the author can be wrong too. But also don't ignore information the thread already contains.

### 6. Reach a verdict
Classify the thread as one of:
- **Valid** — the reviewer's claim holds up against the code. The code should change.
- **Invalid** — the claim doesn't hold up. State precisely what the code does and why the reviewer's reading is off. (Be charitable — describe the misunderstanding, don't dunk on it.)
- **Partially valid** — the kernel of the concern is real but the details are wrong. Examples: correct concern but wrong location, real bug but the proposed fix has its own issues, valid question with an answer the reviewer didn't notice.
- **Needs discussion** — you can't determine validity from the code alone because it depends on intent, requirements, runtime configuration, or domain context. State exactly what information is missing.

## Output Format

```
# PR Comment Review

## Source
[GitHub mode: owner/repo#number — title, then PR URL]
[File mode: path to the file]

## Summary
[N threads reviewed. Breakdown: X Valid, Y Invalid, Z Partially valid, W Needs discussion. One sentence on the overall read of the review round.]

## Threads

### Thread 1: [short label — file:line, or short label — (unspecified) in file mode]
- **Reviewer**: @login   (or "unknown" in file mode if the source didn't say)
- **Status**: Unresolved [, Outdated]   (omit entirely in file mode — the source doesn't carry resolution state)
- **Location**: path:line ([comment URL])   (URL omitted in file mode; location may be "unspecified — located by search" with the file:line you found)
- **Reviewer's claim**: [1-2 sentence summary of the core assertion]
- **What the code actually does**: [neutral, first-principles read — written before evaluating]
- **Verdict**: Valid | Invalid | Partially valid | Needs discussion
- **Reasoning**: [why this verdict, citing specific code with file:line references]
- **Suggested fix**: [described, not applied — only for Valid and Partially valid]
- **Reply draft**: [optional — a balanced, non-defensive draft the user can post, especially useful for Invalid and Partially valid verdicts]

[Repeat for each thread]
```

Fields that the source doesn't carry should be omitted, not faked. File-mode reports won't have comment URLs, resolution state, or sometimes reviewer handles — that's expected, not a defect.

## Guidelines

- **Read the code before the comment.** The order in Phase 2 is deliberate. Stating what the code does *before* fully internalizing the reviewer's framing is the main mechanism for staying unbiased. Don't shortcut it.
- **Don't auto-agree.** A confidently written comment is still a claim that needs verification. Reviewers who are usually right are sometimes wrong, and the cost of agreeing with a wrong comment is real code damage.
- **Don't auto-dismiss.** A poorly worded or partly mistaken comment can still contain a real concern. Find the kernel before deciding it's invalid.
- **Be charitable about misreadings.** If a reviewer misread the code, describe the misunderstanding plainly without making it sound like a gotcha. The goal is to clarify, not to score points.
- **Cite specific code for every verdict.** A verdict without `file:line` evidence is just another unverified claim.
- **Nits are still claims.** Style and nit comments are evaluated the same way — but they're low-stakes, so the Suggested fix and Reply draft can be brief.
- **Outdated threads need extra care.** If `isOutdated == true`, the cited line may have moved. Find the relevant code by content, evaluate based on the current state, and explicitly note when an outdated thread is genuinely stale (the concern no longer applies because the code has changed).
- **Don't pad.** If a thread is unambiguously Valid, say so concisely. If a thread is unambiguously Invalid, explain the misreading and move on. Don't manufacture nuance to look thorough.
- **Don't apply edits.** This skill produces a report and stops. The user decides what to do with the verdicts.
- **File mode: list before evaluating.** When the source is a file, surface the extracted comments back to the user as a brief checklist before diving into Phase 2 — it lets them catch parsing mistakes (missed comments, picked up something that wasn't a comment) before you spend tokens evaluating the wrong inputs.
- **File mode: don't invent metadata.** If the source file doesn't carry resolution state, reviewer handles, or comment URLs, leave those fields out. A fabricated `@reviewer` or a guessed "Unresolved" status is worse than no field.

## Input

$ARGUMENTS
