#!/usr/bin/env python3
"""Check that a report keeps the skill's output template verbatim.

This is the regression the STE change most plausibly breaks: an agent told to
simplify its language can also "simplify" the headings and field labels that the
eval assertions and any downstream tooling parse. Every string below must appear
exactly as the SKILL.md template writes it.
"""
import json
import re
import sys
from pathlib import Path

TEMPLATES = {
    "sc": {
        "headings": ["# Codebase Audit Report", "## Summary", "## Findings",
                     "## Issues Not Found"],
        "fields": ["**Category**", "**Location**", "**Problem**", "**Evidence**",
                   "**Suggested fix**", "**Effort**"],
        "finding_re": r"^###\s+\[?(Critical|High|Medium|Low)\]?\b",
        "enums": {
            "severity": ["Critical", "High", "Medium", "Low"],
            "category": ["Correctness", "Security", "Error Handling",
                         "Performance", "Maintainability"],
            "effort": ["Small", "Medium", "Large", "Architectural"],
        },
    },
    "rebug": {
        "headings": ["# Issue Validation Report", "## Summary", "## Detailed Findings"],
        "fields": ["**Original severity**", "**Verdict**", "**Investigation**",
                   "**Fix assessment**"],
        # Accept "### Issue 3:" as well as the template's "### Issue:", but record
        # the numbering as a deviation — it is the kind of drift that breaks a
        # downstream parser keyed on the literal template.
        "finding_re": r"^###\s+Issue\s*\d*\s*:",
        "canonical_finding_prefix": "### Issue:",
        "enums": {
            "verdict": ["Confirmed, reseveritied", "Confirmed", "Partially valid",
                        "Disputed", "Needs more context"],
        },
    },
    "apc": {
        "headings": ["# PR Comment Review", "## Source", "## Summary", "## Threads"],
        "fields": ["**Reviewer**", "**Location**", "**Reviewer's claim**",
                   "**What the code actually does**", "**Verdict**", "**Reasoning**"],
        "finding_re": r"^###\s+Thread\s+\d+",
        "enums": {
            "verdict": ["Partially valid", "Needs discussion", "Valid", "Invalid"],
        },
    },
    "pr": {
        "headings": ["# PR Independent Review", "## PR",
                     "## What this PR does (from the diff)",
                     "## What the author says it does", "## Summary", "## Findings",
                     "## Issues Not Found", "## Open Questions"],
        "fields": ["**Category**", "**Location**", "**Problem**", "**Evidence**",
                   "**Suggested fix**", "**Effort**"],
        "finding_re": r"^###\s+\[?(Critical|High|Medium|Low)\]?\b",
        "enums": {
            "severity": ["Critical", "High", "Medium", "Low"],
            "effort": ["Small", "Medium", "Large", "Architectural"],
        },
        # present only in the current working-tree version of the skill
        "optional_headings": ["## The underlying problem"],
    },
}


def kind_of(run_id: str) -> str:
    for k in ("sc", "rebug", "apc", "pr"):
        if run_id.startswith(k):
            return k
    raise SystemExit(f"cannot infer template for {run_id}")


def score(run_id: str, md: str) -> dict:
    t = TEMPLATES[kind_of(run_id)]
    missing_h = [h for h in t["headings"] if h not in md]
    missing_f = [f for f in t["fields"] if f not in md]
    findings = re.findall(t["finding_re"], md, flags=re.M | re.I)
    n_findings = len(findings)
    # The template writes the severity placeholder as [SEVERITY], so [CRITICAL]
    # and [Critical] are both defensible readings. Track the casing separately
    # instead of failing the template check on it.
    heading_format = None
    if t.get("canonical_finding_prefix"):
        n_canon = len(re.findall(rf"^{re.escape(t['canonical_finding_prefix'])}",
                                 md, flags=re.M))
        heading_format = "template" if n_canon == n_findings else (
            f"deviates: {n_findings - n_canon}/{n_findings} numbered")

    severity_case = None
    if t["enums"].get("severity"):
        allowed = set(t["enums"]["severity"])
        seen = {f if isinstance(f, str) else f[0] for f in findings}
        severity_case = "template" if seen <= allowed else "deviates: " + ", ".join(
            sorted(s for s in seen if s not in allowed))

    enum_violations = {}
    for name, allowed in t["enums"].items():
        label = {"severity": None, "effort": "**Effort**", "category": "**Category**",
                 "verdict": "**Verdict**"}.get(name)
        if label:
            vals = re.findall(rf"{re.escape(label)}:?\s*\**\s*([^\n|]+)", md)
            bad = []
            for v in vals:
                v = v.strip().strip("*").strip()
                if not any(v.startswith(a) for a in allowed):
                    bad.append(v[:60])
            if bad:
                enum_violations[name] = bad

    checks = len(t["headings"]) + len(t["fields"])
    passed = checks - len(missing_h) - len(missing_f)
    return {
        "findings_count": n_findings,
        "heading_format": heading_format,
        "severity_case": severity_case,
        "missing_headings": missing_h,
        "missing_fields": missing_f,
        "optional_headings_present": [h for h in t.get("optional_headings", []) if h in md],
        "enum_violations": enum_violations,
        "template_checks": checks,
        "template_passed": passed,
        "template_pass_rate": round(passed / checks, 3),
        "report_chars": len(md),
    }


if __name__ == "__main__":
    out = {}
    for p in sys.argv[1:]:
        path = Path(p)
        run_id = path.parent.name
        out[run_id] = score(run_id, path.read_text())
    print(json.dumps(out, indent=2))
