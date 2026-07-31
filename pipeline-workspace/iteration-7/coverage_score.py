#!/usr/bin/env python3
"""Score large-target properties of a performance-audit report.

The bazaar target exists to test behaviors a small repo cannot:
  subsystem_coverage    fraction of issue-bearing subsystems with at least one
                        found planted issue (ranked, cold-noted, or dismissed)
  restraint_violations  ranked findings whose Location field sits in a
                        zero-plant subsystem (services/inventory, libs/eventkit)
  cross_subsystem       recall over the cross_subsystem-flagged issues, plus
                        whether each matched finding's text names the caller
                        service (the synthesis the fan-out must not lose)
  p4_discipline         matched tier-4 plants actually labeled P4

Subsystem of an issue = first two path components of its file.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gt_score        # noqa: E402
import priority_score  # noqa: E402

ZERO_PLANT_SUBSYSTEMS = ("services/inventory", "libs/eventkit")
CALLER_HINTS = ("pricing", "checkout", "catalog", "gateway", "settlement")


def subsystem(path: str) -> str:
    return "/".join(path.split("/")[:2])


def location_field(block: str) -> str:
    m = re.search(r"\*\*Location\*\*:?(.*)", block)
    return m.group(1) if m else ""


def score(md: str, gt: dict) -> dict:
    line_window = gt.get("line_window", 12)
    section = priority_score.findings_section(md)
    ranked = gt_score.split_findings(section)
    gt_result = gt_score.score(section, gt, line_window=line_window)
    pr = priority_score.score(md, gt)

    found_ids = ({m["id"] for m in gt_result["matched"]}
                 | set(pr["noted_in_cold_notes"])
                 | set(pr["dismissed_in_not_found"]))
    issues = {i["id"]: i for i in gt["issues"]}

    bearing = sorted({subsystem(i["file"]) for i in gt["issues"]})
    covered = sorted({subsystem(issues[i]["file"]) for i in found_ids})
    coverage = round(len(covered) / len(bearing), 3) if bearing else None

    violations = []
    for i, block in enumerate(ranked):
        loc = location_field(block)
        for zp in ZERO_PLANT_SUBSYSTEMS:
            if zp in loc:
                violations.append({"position": i,
                                   "finding": block.splitlines()[0][:90],
                                   "subsystem": zp})

    cs_issues = [i for i in gt["issues"] if i.get("cross_subsystem")]
    cs_found = [i["id"] for i in cs_issues if i["id"] in found_ids]
    idx = {m["id"]: m["finding_index"] for m in gt_result["matched"]}
    caller_cited = []
    for iid in cs_found:
        if iid not in idx:
            continue
        block = ranked[idx[iid]].lower()
        callers = [c for c in CALLER_HINTS
                   if c in block and c not in issues[iid]["file"]]
        caller_cited.append({"id": iid, "callers_named": callers})

    p4 = [i for i in gt["issues"] if i.get("expected_priority_tier") == 4]
    p4_matched = [i["id"] for i in p4 if i["id"] in idx]
    p4_correct = 0
    for iid in p4_matched:
        tm = priority_score.TIER_RE.search(ranked[idx[iid]])
        if tm and int(tm.group(1)) == 4:
            p4_correct += 1

    return {
        "subsystems_bearing": bearing,
        "subsystems_covered": covered,
        "subsystem_coverage": coverage,
        "restraint_violations": violations,
        "restraint_violation_count": len(violations),
        "cross_subsystem_total": len(cs_issues),
        "cross_subsystem_found": len(cs_found),
        "cross_subsystem_recall": (round(len(cs_found) / len(cs_issues), 3)
                                   if cs_issues else None),
        "caller_cited": caller_cited,
        "caller_cited_count": sum(1 for c in caller_cited if c["callers_named"]),
        "p4_total": len(p4),
        "p4_matched": len(p4_matched),
        "p4_labeled_p4": p4_correct,
    }


if __name__ == "__main__":
    gt = json.loads(Path(sys.argv[1]).read_text())
    out = {}
    for p in sys.argv[2:]:
        path = Path(p)
        out[path.parent.name] = score(path.read_text(), gt)
    print(json.dumps(out, indent=2))
