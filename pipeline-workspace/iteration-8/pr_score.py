#!/usr/bin/env python3
"""Score a PR review against the parcelpost planted-issue manifest.

Usage: pr_score.py <ground_truth.json> <run-dir>... [--json out.json]

A report is split into finding blocks: `### ` sections under `## Findings` for
pr-review, or the objects of a JSON array / numbered items for the native
/code-review. Each manifest entry carries regex alternatives; an entry is hit
when every regex of one alternative matches the same block. One block may hit
several issues. Trap hits are candidates only: a regex cannot tell "this is
injectable" from "this is not injectable", so read the block before counting it.
"""
import json
import re
import sys
from pathlib import Path

SEVERITIES = ["Critical", "High", "Medium", "Low"]
DESIGN_CATEGORIES = {"Architectural fit", "Simplicity & economy", "API & interface design",
                     "Scope & PR hygiene"}


def finding_blocks(report: str) -> list[dict]:
    text = report
    m = re.search(r"^## Findings\s*$", report, flags=re.M)
    if m:
        rest = report[m.end():]
        end = re.search(r"^## ", rest, flags=re.M)
        text = rest[: end.start()] if end else rest
        blocks = []
        for part in re.split(r"^### ", text, flags=re.M)[1:]:
            sev = re.match(r"\[?\**(critical|high|medium|low)", part, flags=re.I)
            cat = re.search(r"\*\*Category\*\*:\s*(.+)", part)
            blocks.append({"text": part, "title": part.splitlines()[0].strip(),
                           "severity": sev.group(1).capitalize() if sev else None,
                           "category": cat.group(1).strip() if cat else None})
        return blocks
    fenced = re.search(r"```json\s*(\[.*?\])\s*```", report, flags=re.S)
    raw = fenced.group(1) if fenced else (report if report.lstrip().startswith("[") else None)
    if raw:
        try:
            return [{"text": json.dumps(o), "title": o.get("summary", "")[:90], "severity": None,
                     "category": None} for o in json.loads(raw)]
        except json.JSONDecodeError:
            pass
    parts = re.split(r"^(?:#{2,4} |\*\*\d+\.|\d+\. )", report, flags=re.M)[1:]
    return [{"text": p, "title": p.splitlines()[0].strip()[:90], "severity": None, "category": None}
            for p in parts if p.strip()]


def hits(entry: dict, block: str) -> bool:
    return any(all(re.search(rx, block, flags=re.I | re.S) for rx in alt) for alt in entry["match"])


def score(report: str, gt: dict) -> dict:
    blocks = finding_blocks(report)
    found, severity_ok, severity_seen = {}, 0, 0
    for issue in gt["issues"]:
        matched = [b for b in blocks if hits(issue, b["text"])]
        if not matched:
            continue
        found[issue["id"]] = matched[0]["title"]
        sevs = [b["severity"] for b in matched if b["severity"]]
        if sevs:
            severity_seen += 1
            want = SEVERITIES.index(issue["severity"])
            severity_ok += any(abs(SEVERITIES.index(s) - want) <= 1 for s in sevs)
    by_tier = {}
    for tier in ("impl", "design", "low"):
        ids = [i["id"] for i in gt["issues"] if i["tier"] == tier]
        by_tier[tier] = {"found": [i for i in ids if i in found], "missed": [i for i in ids if i not in found]}
    unmatched = [b["title"] for b in blocks
                 if not any(hits(e, b["text"]) for e in gt["issues"] + gt["traps"])]
    trap_candidates = {t["id"]: [b["title"] for b in blocks if hits(t, b["text"])] for t in gt["traps"]}

    # Design-first ordering: within one severity, does any implementation finding precede a design finding?
    order_violations = 0
    cats = [(b["severity"], b["category"]) for b in blocks if b["severity"] and b["category"]]
    for i, (sev_a, cat_a) in enumerate(cats):
        for sev_b, cat_b in cats[i + 1:]:
            if sev_a == sev_b and cat_a not in DESIGN_CATEGORIES and cat_b in DESIGN_CATEGORIES:
                order_violations += 1
    sev_rank = [SEVERITIES.index(s) for s, _ in cats]

    n = len(gt["issues"])
    impl_n = len(by_tier["impl"]["found"]) + len(by_tier["impl"]["missed"])
    design_n = len(by_tier["design"]["found"]) + len(by_tier["design"]["missed"])
    return {
        "findings": len(blocks),
        "severity_counts": {s: sum(b["severity"] == s for b in blocks) for s in SEVERITIES},
        "recall": round(len(found) / n, 3),
        "recall_impl": round(len(by_tier["impl"]["found"]) / impl_n, 3),
        "recall_design": round(len(by_tier["design"]["found"]) / design_n, 3),
        "missed": by_tier["impl"]["missed"] + by_tier["design"]["missed"] + by_tier["low"]["missed"],
        "severity_accuracy": round(severity_ok / severity_seen, 3) if severity_seen else None,
        "severity_sorted": sev_rank == sorted(sev_rank) if sev_rank else None,
        "design_first_violations": order_violations if cats else None,
        "trap_candidates": {k: v for k, v in trap_candidates.items() if v},
        "unmatched_findings": unmatched,
        "report_chars": len(report),
        "report_words": len(report.split()),
    }


def main(argv):
    out_path = None
    if "--json" in argv:
        i = argv.index("--json")
        out_path = argv[i + 1]
        argv = argv[:i] + argv[i + 2:]
    gt = json.loads(Path(argv[0]).read_text())
    results = {}
    for run in argv[1:]:
        run = Path(run)
        report = run / "report.md"
        if not report.exists():
            print(f"{run.name}: no report.md")
            continue
        s = score(report.read_text(), gt)
        timing = run / "timing.json"
        if timing.exists():
            t = json.loads(timing.read_text())
            s.update({k: t.get(k) for k in ("wall_seconds", "cost_usd", "gh_review_reads",
                                           "gh_mutations", "worktrees_left", "branches_left")})
        results[run.name] = s
        print(f"\n== {run.name}")
        for k, v in s.items():
            print(f"  {k}: {v}")
    if out_path:
        Path(out_path).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main(sys.argv[1:])
