#!/usr/bin/env python3
"""Score an audit report against a planted-issue manifest.

Matching is deliberately loose on line numbers (a +/- window) and requires a
keyword overlap with the planted description, because a finding that names the
right file and the right failure mode is a hit even when the cited line is a few
lines off. Unmatched findings are printed for manual review rather than counted
as false positives automatically — a real extra issue is not a precision loss.
"""
import json
import re
import sys
from pathlib import Path

STOP = {"the", "a", "an", "and", "or", "if", "is", "are", "to", "of", "in", "on",
        "no", "not", "for", "with", "from", "as", "it", "its", "by", "be", "this",
        "that", "every", "all", "any", "per", "than", "then", "so", "but", "at",
        "does", "do", "can", "may", "will", "should", "would", "into", "out",
        "instead", "actually", "wait", "let", "me", "re", "which", "when"}


def keywords(text: str) -> set:
    return {w for w in re.findall(r"[a-z_]{4,}", text.lower()) if w not in STOP}


def split_findings(md: str) -> list:
    """Split a report into findings on '### ' boundaries."""
    parts = re.split(r"^###\s+", md, flags=re.M)[1:]
    return [p for p in parts if p.strip()]


def locations(block: str) -> list:
    """(file, line) pairs cited anywhere in a finding block."""
    out = []
    for m in re.finditer(r"([\w./-]+\.py)(?::(\d+)(?:\s*[-–]\s*(\d+))?)?", block):
        f = m.group(1).lstrip("./")
        start = int(m.group(2)) if m.group(2) else None
        end = int(m.group(3)) if m.group(3) else start
        out.append((f, start, end))
    return out


def score(md: str, gt: dict, line_window: int = 12) -> dict:
    findings = split_findings(md)
    blocks = [(f, locations(f), keywords(f)) for f in findings]

    matched, missed = [], []
    used = set()
    for issue in gt["issues"]:
        gfile = issue["file"].split("/")[-1]
        gline = issue["line"]
        gkw = keywords(issue["description"])
        best = None
        for i, (block, locs, kw) in enumerate(blocks):
            file_hit = any(f.endswith(gfile) for f, _, _ in locs)
            if not file_hit:
                continue
            line_hit = any(
                f.endswith(gfile) and s is not None
                and (s - line_window <= gline <= (e or s) + line_window)
                for f, s, e in locs
            )
            overlap = len(gkw & kw) / max(len(gkw), 1)
            sc = (2 if line_hit else 0) + overlap
            if overlap >= 0.18 or (line_hit and overlap >= 0.10):
                if best is None or sc > best[1]:
                    best = (i, sc, round(overlap, 2), line_hit)
        if best:
            used.add(best[0])
            title = findings[best[0]].splitlines()[0][:90]
            matched.append({"id": issue["id"], "finding": title,
                            "overlap": best[2], "line_hit": best[3]})
        else:
            missed.append({"id": issue["id"], "file": issue["file"],
                           "line": gline, "description": issue["description"]})

    extras = [{"index": i, "title": findings[i].splitlines()[0][:90],
               "locations": [f"{f}:{s}" for f, s, _ in locs[:3]]}
              for i, (f_, locs, _) in enumerate(blocks) if i not in used]

    traps = []
    for trap in gt.get("false_positive_traps", []):
        tfile = trap["file"].split("/")[-1]
        tline = trap["line"]
        for i, (block, locs, kw) in enumerate(blocks):
            hit = any(f.endswith(tfile) and s is not None
                      and abs(s - tline) <= line_window for f, s, e in locs)
            if hit:
                traps.append({"trap": trap["description"][:70],
                              "finding": findings[i].splitlines()[0][:90],
                              "note": "cites trap location — verify manually "
                                      "whether it is flagged as a bug"})
    total = gt["total_planted"]
    return {
        "planted_total": total,
        "planted_found": len(matched),
        "recall": round(len(matched) / total, 3),
        "findings_reported": len(findings),
        "matched": matched,
        "missed": missed,
        "unmatched_findings": extras,
        "trap_location_hits": traps,
    }


if __name__ == "__main__":
    gt = json.loads(Path(sys.argv[1]).read_text())
    out = {}
    for p in sys.argv[2:]:
        path = Path(p)
        out[path.parent.name] = score(path.read_text(), gt)
    print(json.dumps(out, indent=2))
