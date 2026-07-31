#!/usr/bin/env python3
"""Score a performance-audit report's prioritization against ground truth.

The perform-like-jeff-dean skill's core promise is ordering: low-effort,
high-impact findings first. This scorer measures that promise directly.

Only findings inside the `## Findings` section count as ranked. A trap or a
planted cold-path issue mentioned in `## Cold-Code Notes` is deliberately not
penalized — that section is the skill's designated place for down-prioritized
observations.

Metrics:
  pairwise_order_accuracy  Over all matched planted-issue pairs (a, b) whose
                           expected tiers differ, the fraction where the
                           more-important issue appears earlier in the report.
                           1.0 = perfect ordering, 0.5 = random, null = no pairs.
  top3_mean_expected_tier  Mean expected tier of the first three ranked
                           findings; an unmatched finding counts as tier 3.
  impact/effort/tier_label_accuracy  Fraction of matched findings whose parsed
                           label equals the ground-truth expectation.
  fp_trap_hits             Traps cited within the line window by a RANKED
                           finding (mechanical, unlike gt_score's manual-review
                           trap list).
  strict_precision         matched ranked findings / total ranked findings.
                           Valid because the perfburn ground truth is exhaustive
                           by construction (see the corrections protocol).
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import gt_score  # noqa: E402

IMPACT_RE = re.compile(r"\*\*Impact\*\*:?\s*\**\s*(High|Medium|Low)")
EFFORT_RE = re.compile(r"\*\*Effort\*\*:?\s*\**\s*(Small|Medium|Large|Architectural)")
TIER_RE = re.compile(r"^\[?P([1-4])\]?\b")


def findings_section(md: str) -> str:
    """The text of `## Findings` up to the next `## ` heading."""
    m = re.search(r"^##\s+Findings\s*$(.*?)(?=^##\s|\Z)", md, flags=re.M | re.S)
    return m.group(1) if m else ""


def cold_notes_section(md: str) -> str:
    """The text of `## Cold-Code Notes` up to the next `## ` heading."""
    m = re.search(r"^##\s+Cold-Code Notes\s*$(.*?)(?=^##\s|\Z)",
                  md, flags=re.M | re.S)
    return m.group(1) if m else ""


def not_found_section(md: str) -> str:
    """The text of `## Not Found` up to the next `## ` heading."""
    m = re.search(r"^##\s+Not Found\s*$(.*?)(?=^##\s|\Z)",
                  md, flags=re.M | re.S)
    return m.group(1) if m else ""


def cited_in(text: str, gfile: str, gline: int, window: int) -> bool:
    return any(f.endswith(gfile) and s is not None
               and (s - window <= gline <= (e or s) + window)
               for f, s, e in gt_score.locations(text))


def score(md: str, gt: dict) -> dict:
    line_window = gt.get("line_window", 12)
    section = findings_section(md)
    ranked = gt_score.split_findings(section)
    gt_result = gt_score.score(section, gt, line_window=line_window)

    tiers = {i["id"]: i.get("expected_priority_tier") for i in gt["issues"]}
    impacts = {i["id"]: i.get("expected_impact") for i in gt["issues"]}
    efforts = {i["id"]: i.get("expected_effort") for i in gt["issues"]}

    matched = [m for m in gt_result["matched"] if tiers.get(m["id"]) is not None]

    pairs = ordered = 0
    for a in matched:
        for b in matched:
            if tiers[a["id"]] < tiers[b["id"]]:
                pairs += 1
                if a["finding_index"] < b["finding_index"]:
                    ordered += 1
    pairwise = round(ordered / pairs, 3) if pairs else None

    index_to_tier = {m["finding_index"]: tiers[m["id"]] for m in matched}
    top3 = [index_to_tier.get(i, 3) for i in range(min(3, len(ranked)))]
    top3_mean = round(sum(top3) / len(top3), 2) if top3 else None

    impact_ok = impact_bad = impact_unparsed = 0
    effort_ok = effort_bad = effort_unparsed = 0
    tier_ok = tier_bad = tier_unparsed = 0
    mismatches = []
    for m in matched:
        block = ranked[m["finding_index"]]
        im = IMPACT_RE.search(block)
        if not im:
            impact_unparsed += 1
        elif im.group(1) == impacts[m["id"]]:
            impact_ok += 1
        else:
            impact_bad += 1
            mismatches.append({"id": m["id"], "field": "impact",
                               "expected": impacts[m["id"]], "reported": im.group(1)})
        ef = EFFORT_RE.search(block)
        if not ef:
            effort_unparsed += 1
        elif ef.group(1) == efforts[m["id"]]:
            effort_ok += 1
        else:
            effort_bad += 1
            mismatches.append({"id": m["id"], "field": "effort",
                               "expected": efforts[m["id"]], "reported": ef.group(1)})
        tm = TIER_RE.search(block)
        if not tm:
            tier_unparsed += 1
        elif int(tm.group(1)) == tiers[m["id"]]:
            tier_ok += 1
        else:
            tier_bad += 1
            mismatches.append({"id": m["id"], "field": "tier",
                               "expected": tiers[m["id"]],
                               "reported": int(tm.group(1))})

    matched_indices = {m["finding_index"] for m in matched}

    # A trap counts as hit only when an UNMATCHED ranked finding cites its
    # location. A finding about a real planted issue often cites a
    # trap-adjacent line as supporting context (a config default, a test that
    # documents cardinality); that is good citation practice, not a false
    # positive.
    trap_hits = []
    for trap in gt.get("false_positive_traps", []):
        tfile = trap["file"].split("/")[-1]
        tline = trap["line"]
        for i, block in enumerate(ranked):
            if i in matched_indices:
                continue
            if cited_in(block, tfile, tline, line_window):
                trap_hits.append({"trap_id": trap["id"],
                                  "finding": block.splitlines()[0][:90],
                                  "position": i})
                break

    # A cold-path planted issue that the report records in Cold-Code Notes is
    # found — that placement is what the skill prescribes for it. A planted
    # issue dismissed in Not Found with a citation at its location was also
    # seen and judged; count it as found but track it separately so a
    # dismissal never masquerades as a ranked finding.
    notes = cold_notes_section(md)
    dismissals = not_found_section(md)
    matched_ids = {m["id"] for m in gt_result["matched"]}
    noted = [i["id"] for i in gt["issues"]
             if i["id"] not in matched_ids
             and cited_in(notes, i["file"].split("/")[-1], i["line"], line_window)]
    dismissed = [i["id"] for i in gt["issues"]
                 if i["id"] not in matched_ids and i["id"] not in noted
                 and cited_in(dismissals, i["file"].split("/")[-1], i["line"],
                              line_window)]

    def rate(ok, bad):
        return round(ok / (ok + bad), 3) if (ok + bad) else None

    return {
        "ranked_findings": len(ranked),
        "matched_count": len(matched),
        "orderable_pairs": pairs,
        "pairwise_order_accuracy": pairwise,
        "top3_mean_expected_tier": top3_mean,
        "impact_label_accuracy": rate(impact_ok, impact_bad),
        "impact_unparsed": impact_unparsed,
        "effort_label_accuracy": rate(effort_ok, effort_bad),
        "effort_unparsed": effort_unparsed,
        "tier_label_accuracy": rate(tier_ok, tier_bad),
        "tier_unparsed": tier_unparsed,
        "strict_precision": (round(len(matched_indices) / len(ranked), 3)
                             if ranked else None),
        "fp_traps_total": len(gt.get("false_positive_traps", [])),
        "fp_trap_hits": len(trap_hits),
        "trap_details": trap_hits,
        "label_mismatches": mismatches,
        "recall_ranked": gt_result["recall"],
        "noted_in_cold_notes": noted,
        "dismissed_in_not_found": dismissed,
        "recall_total": round(
            (len(matched_ids) + len(noted) + len(dismissed))
            / gt["total_planted"], 3),
        "missed": [m for m in gt_result["missed"]
                   if m["id"] not in noted and m["id"] not in dismissed],
    }


if __name__ == "__main__":
    gt = json.loads(Path(sys.argv[1]).read_text())
    out = {}
    for p in sys.argv[2:]:
        path = Path(p)
        out[path.parent.name] = score(path.read_text(), gt)
    print(json.dumps(out, indent=2))
