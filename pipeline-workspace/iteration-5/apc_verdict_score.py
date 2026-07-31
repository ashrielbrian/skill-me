#!/usr/bin/env python3
"""Score address-pr-comments verdicts against the expected answer key.

The synthetic comments file is built from eventbus's ground truth: five comments
restate a planted issue (expected Valid) and two restate a false-positive trap,
where a misleading code comment claims a bug the code does not have (expected
Invalid). "Partially valid" is accepted on a Valid item, because a comment can
describe a real defect and still get the mechanism wrong — thread 6 does exactly
that, and calling it out is better work, not worse.
"""
import json
import re
import sys
from pathlib import Path

KEY = [
    {"loc": "sqlite.py:47", "expect": "Invalid", "why": "FP trap: parameterized query"},
    {"loc": "dedup.py:34", "expect": "Invalid", "why": "FP trap: evicts oldest, which is correct"},
    {"loc": "bus.py:25", "expect": "Valid", "why": "EB-PERF-1 unbounded dead letter"},
    {"loc": "webhook.py:43", "expect": "Valid", "why": "EB-PERF-4 blocking urlopen"},
    {"loc": "logging.py:24", "expect": "Valid", "why": "EB-SEC-1 payload in logs"},
    {"loc": "bus.py:52", "expect": "Valid", "why": "EB-COR-2 stop() drops queued events"},
    {"loc": "sqlite.py:40", "expect": "Valid", "why": "EB-PERF-3 commit per insert"},
]
PARTIAL_OK = {"Valid": {"Valid", "Partially valid"}, "Invalid": {"Invalid"}}


def verdicts(md: str) -> list:
    """(thread heading, verdict) in document order."""
    out = []
    blocks = re.split(r"^###\s+", md, flags=re.M)[1:]
    for b in blocks:
        head = b.splitlines()[0]
        m = re.search(r"\*\*Verdict\*\*:?\s*\**\s*([A-Za-z ]+?)\**\s*(?:\(|$|\n)", b, re.M)
        out.append((head, m.group(1).strip() if m else None))
    return out


def score(md: str) -> dict:
    found = verdicts(md)
    rows, correct = [], 0
    for item in KEY:
        f, ln = item["loc"].split(":")
        hit = next(((h, v) for h, v in found
                    if f in h and (ln in h or True) and item["loc"] in h), None)
        if hit is None:
            hit = next(((h, v) for h, v in found if f in h), None)
        got = hit[1] if hit else None
        ok = got in PARTIAL_OK[item["expect"]] if got else False
        correct += ok
        rows.append({"comment": item["loc"], "expected": item["expect"],
                     "got": got, "ok": ok, "why": item["why"]})
    return {
        "threads_reported": len(found),
        "expected_threads": len(KEY),
        "verdict_correct": correct,
        "verdict_accuracy": round(correct / len(KEY), 3),
        "fp_traps_caught": sum(1 for r in rows
                               if r["expected"] == "Invalid" and r["ok"]),
        "fp_traps_total": sum(1 for r in rows if r["expected"] == "Invalid"),
        "rows": rows,
    }


if __name__ == "__main__":
    out = {}
    for p in sys.argv[1:]:
        path = Path(p)
        out[path.parent.name] = score(path.read_text())
    print(json.dumps(out, indent=2))
