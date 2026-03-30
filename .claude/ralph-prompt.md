Iteratively improve spring_clean/SKILL.md and rebug/SKILL.md skills. Read CLAUDE.md for full methodology.

Baseline commit: c7a5984. Current recall 96.4% (27/28 planted issues found), precision 100%. Missed SEC-16 (error context logging). Test against test-repo/taskflow with ground truth at pipeline-workspace/iteration-2/ground_truth.json.

METRICS TO OPTIMIZE (in priority order):
1. Low false positives - never report issues that do not exist
2. High precision - every reported issue should be a real, actionable problem
3. High recall - find as many real issues as possible without sacrificing precision

Each iteration:
1. Identify weakest metric or blind spot
2. Edit spring_clean/SKILL.md and/or rebug/SKILL.md
3. Run pipeline (current vs baseline from previous commit) on test-repo/taskflow
4. Score against ground_truth.json for recall, precision, false positives
5. If improved: git commit with metrics in commit message, git push
6. If regressed: revert and try a different approach

CREATING NEW TEST PROJECTS: You should create additional test projects beyond test-repo/taskflow to avoid overfitting and to stress-test the skills. Be creative - try different languages, frameworks, project sizes, and bug categories. Include subtle bugs that are easy to miss, clean code that should NOT be flagged, and edge cases. Write a ground_truth.json for each new test project. Use multiple test projects when evaluating each iteration.

Signal completion with promise tag SKILLS OPTIMIZED after 3 successful improvements or when metrics plateau.
