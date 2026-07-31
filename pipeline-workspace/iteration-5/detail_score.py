#!/usr/bin/env python3
"""Measure information density, not just conformance.

The risk in an STE rewrite is not a broken template — it is a report that obeys
every sentence limit and quietly drops the facts that make a finding actionable.
The side-by-side on eventbus showed exactly that: the baseline said "up to 10 000
queued events" and named a race against `close()`; the STE arm said "every event
in the queue is lost". Both are true; only one is usable.

So these proxies count the concrete, checkable content in a report:
  - quantities      numbers with or without units (10000, 2.1%, 30 s, 100 ms)
  - citations       file:line references
  - code refs       inline-code spans (symbols, paths, snippets)
  - causal links    connectives that carry a consequence ("because", "so that")
"""
import json
import re
import sys
from pathlib import Path

QUANTITY = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:[ ,_]\d{3})+|\d+(?:\.\d+)?)"
    r"\s*(?:%|ms|s\b|us\b|ns\b|kb|mb|gb|gib|mib|kib|x\b|times\b|"
    r"seconds?\b|minutes?\b|hours?\b|days?\b|bytes?\b|entries\b|events?\b|"
    r"rows?\b|items\b|calls?\b|requests?\b|lines?\b)?", re.I)
CITATION = re.compile(r"[\w./-]+\.(?:py|ts|js|md|yaml|yml|toml|json|sh):\d+")
CODE_SPAN = re.compile(r"`[^`]+`")
CAUSAL = re.compile(
    r"\b(?:because|so that|therefore|as a result|which means|so the|so every|"
    r"then the|leads to|causes|results in|until|unless|whenever)\b", re.I)


def score(md: str) -> dict:
    body = re.sub(r"```.*?```", " ", md, flags=re.S)   # exclude fenced evidence
    findings = max(len(re.findall(r"^###\s+", md, flags=re.M)), 1)

    # A bare year-like or version-like token is not a measurement; require the
    # number to sit in prose, not inside a code span or a path.
    prose = CODE_SPAN.sub(" ", CITATION.sub(" ", body))
    quantities = [m.group(0).strip() for m in QUANTITY.finditer(prose)
                  if m.group(0).strip() and not re.fullmatch(r"\d", m.group(0).strip())]
    citations = CITATION.findall(body)
    code_refs = CODE_SPAN.findall(body)
    causal = CAUSAL.findall(body)

    return {
        "findings": findings,
        "chars": len(md),
        "chars_per_finding": round(len(md) / findings),
        "quantities": len(quantities),
        "quantities_per_finding": round(len(quantities) / findings, 2),
        "quantity_examples": quantities[:14],
        "citations": len(citations),
        "citations_per_finding": round(len(citations) / findings, 2),
        "unique_files_cited": len({c.split(":")[0] for c in citations}),
        "code_refs": len(code_refs),
        "code_refs_per_finding": round(len(code_refs) / findings, 2),
        "causal_links": len(causal),
        "causal_per_finding": round(len(causal) / findings, 2),
    }


if __name__ == "__main__":
    out = {}
    for p in sys.argv[1:]:
        path = Path(p)
        out[path.parent.name] = score(path.read_text())
    print(json.dumps(out, indent=2))
