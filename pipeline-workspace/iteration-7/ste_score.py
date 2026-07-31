#!/usr/bin/env python3
"""Measure ASD-STE100 conformance of a generated report.

Scores prose only. Code fences, inline code, headings, and bullet field labels
are stripped first, because the skills explicitly exempt them: a file path or a
code snippet is a technical name, and the report template's field names must stay
verbatim so downstream tooling keeps parsing.

Checks, each mapped to a rule in ASD-STE100 Part 1:
  - sentence length      (procedural <= 20 words, descriptive <= 25)
  - non-approved words   (writing rule: one approved word per meaning)
  - passive voice        (writing rule: use the active voice)
  - -ing openers         (writing rule: no gerund/participial clause openers)
"""
import json
import re
import sys
from pathlib import Path

# Non-approved words with their STE-approved replacement. Restricted to words
# the skills' STE sections name plus close relatives, so the metric measures the
# instruction we actually gave, not a wider dictionary the skill never listed.
NON_APPROVED = {
    "utilize": "use", "utilizes": "use", "utilizing": "use", "utilization": "use",
    "leverage": "use", "leverages": "use", "leveraging": "use",
    "prior to": "before", "in order to": "to", "due to the fact": "because",
    "approximately": "about", "ensure": "make sure", "ensures": "make sure",
    "ensuring": "make sure", "obtain": "get", "obtains": "get",
    "sufficient": "enough", "insufficient": "not enough", "numerous": "many",
    "commence": "start", "commences": "start", "terminate": "stop",
    "terminates": "stop", "assist": "help", "assists": "help",
    "permit": "let", "permits": "let", "demonstrate": "show",
    "demonstrates": "show", "facilitate": "help", "facilitates": "help",
    "additional": "more", "additionally": "also", "attempt": "try",
    "attempts": "try", "subsequently": "then", "consequently": "so",
    "furthermore": "also", "moreover": "also", "regarding": "about",
    "aforementioned": "this", "endeavour": "try", "endeavor": "try",
    "initiate": "start", "initiates": "start", "modify": "change",
    "modifies": "change", "eliminate": "remove", "eliminates": "remove",
    "necessitate": "need", "necessitates": "need", "requisite": "needed",
    "myriad": "many", "plethora": "many", "vast majority": "most",
}

# Metaphor / idiom / slang tells. STE bans figurative language outright; these are
# the ones that actually turn up in code-review prose.
IDIOMS = [
    "silver bullet", "low-hanging fruit", "moving parts", "footgun", "foot-gun",
    "code smell", "rabbit hole", "band-aid", "band aid", "kick the can",
    "sweep under", "shoot yourself", "shoot itself", "bite you", "bites you",
    "come back to bite", "house of cards", "time bomb",
    "landmine", "land mine", "minefield", "papering over", "paper over",
    "glosses over", "hand-wavy", "hand wavy", "plays fast and loose",
    "solution in search of", "brittle", "hairy", "gnarly", "janky", "footprint",
    "blast radius", "boil the ocean", "yak shaving", "bikeshed",
    "under the hood", "out of the box", "down the line", "on the fly",
]

BE_FORMS = r"(?:is|are|was|were|be|been|being|gets|got)"
PASSIVE = re.compile(
    rf"\b{BE_FORMS}\s+(?:not\s+|never\s+|also\s+|already\s+|silently\s+|only\s+)?"
    r"([a-z]+(?:ed|wn|en| built|held|kept|left|lost|read|sent|set|shown|thrown))\b"
)
# Past participles that are adjectives in practice, not passive constructions.
# STE permits a past participle as an adjective ("the failed handler").
ADJ_PARTICIPLES = {
    "based", "related", "supposed", "used", "needed", "limited", "detailed",
    "expected", "required", "intended", "advanced", "mixed", "involved",
    "concerned", "interested", "aligned", "unbounded", "undocumented",
    "unhandled", "uncaught", "unused", "unreachable", "hardcoded", "deprecated",
    "correct", "incorrect", "present", "absent", "shown", "given", "left",
    "often", "even", "open", "seldom", "sudden", "golden",
}

ING_OPENER = re.compile(r"^(?:And\s+|But\s+|So\s+|However,?\s+|Also,?\s+)?([A-Z][a-z]+ing)\b")
NOUNISH = re.compile(r"^[a-z][a-z_]*$")


def strip_exempt(md: str) -> str:
    """Remove everything the STE sections exempt from the rules."""
    md = re.sub(r"```.*?```", " ", md, flags=re.S)          # fenced code
    md = re.sub(r"`[^`]*`", " CODE ", md)                    # inline code
    md = re.sub(r"^\s{0,3}#{1,6} .*$", " ", md, flags=re.M)  # headings
    md = re.sub(r'"[^"]{0,400}"', " QUOTE ", md)             # quoted source text
    # Bullet field labels from the report templates ("- **Category**: ...").
    md = re.sub(r"^\s*[-*]\s*\*\*[^*]+\*\*:\s*", " ", md, flags=re.M)
    md = re.sub(r"^\s*[-*]\s+", " ", md, flags=re.M)
    md = re.sub(r"^\s*\|.*$", " ", md, flags=re.M)           # tables
    md = re.sub(r"https?://\S+", " URL ", md)
    md = re.sub(r"\b[\w./-]+\.(?:py|md|json|ts|js|toml|yaml|yml|sh)(?::\d+)?", " PATH ", md)
    md = re.sub(r"\*\*|\*|__", "", md)
    return md


def sentences(prose: str) -> list[str]:
    """Split into sentences, one markdown line at a time.

    Line-at-a-time matters: these reports are lists and blockquotes, so joining
    a paragraph first merges the tail of one bullet into the head of the next and
    reports a 48-word "sentence" that no one wrote.
    """
    out = []
    for line in prose.splitlines():
        line = " ".join(line.replace(">", " ").split())
        if not line:
            continue
        for s in re.split(r"(?<=[.!?:;])\s+(?=[A-Z(])", line):
            s = s.strip()
            if len(s.split()) >= 3:
                out.append(s)
    return out


def score(md: str) -> dict:
    prose = strip_exempt(md)
    sents = sentences(prose)
    lower = prose.lower()

    lengths = [len(s.split()) for s in sents]
    over_25 = [s for s in sents if len(s.split()) > 25]
    over_20 = [s for s in sents if len(s.split()) > 20]

    word_hits = []
    for term, repl in NON_APPROVED.items():
        pat = rf"\b{re.escape(term)}\b" if " " not in term else re.escape(term)
        n = len(re.findall(pat, lower))
        if n:
            word_hits.append({"term": term, "approved": repl, "count": n})

    idiom_hits = [{"term": t, "count": lower.count(t)} for t in IDIOMS if t in lower]

    passive_hits = []
    for s in sents:
        for m in PASSIVE.finditer(s):
            if m.group(1).lower() in ADJ_PARTICIPLES:
                continue
            passive_hits.append({"match": m.group(0), "sentence": s[:140]})

    ing_hits = []
    for s in sents:
        m = ING_OPENER.match(s)
        if m and m.group(1).lower() not in {"nothing", "something", "everything"}:
            ing_hits.append({"opener": m.group(1), "sentence": s[:140]})

    # Noun clusters (STE: max 3 nouns together) are deliberately not scored.
    # Detecting them needs part-of-speech tagging; a "not a stop word" heuristic
    # counts verbs and adjectives too ("review round finds four") and produced a
    # number that tracked prose volume rather than noun stacking.
    n = max(len(sents), 1)
    return {
        "sentences": len(sents),
        "words": sum(lengths),
        "mean_sentence_words": round(sum(lengths) / n, 1),
        "max_sentence_words": max(lengths) if lengths else 0,
        "over_25_words": len(over_25),
        "over_25_rate": round(len(over_25) / n, 3),
        "over_20_words": len(over_20),
        "over_20_rate": round(len(over_20) / n, 3),
        "non_approved_total": sum(h["count"] for h in word_hits),
        "non_approved_terms": sorted(word_hits, key=lambda h: -h["count"]),
        "idiom_total": sum(h["count"] for h in idiom_hits),
        "idiom_terms": idiom_hits,
        "passive_total": len(passive_hits),
        "passive_per_100_sentences": round(100 * len(passive_hits) / n, 1),
        "passive_examples": passive_hits[:12],
        "ing_opener_total": len(ing_hits),
        "ing_opener_examples": ing_hits[:12],
        "longest_sentences": sorted(over_25, key=lambda s: -len(s.split()))[:6],
    }


STOP = {
    "the", "a", "an", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "is", "are", "was", "were", "be", "been", "being", "to",
    "of", "in", "on", "at", "by", "for", "with", "from", "as", "it", "its",
    "not", "no", "so", "can", "cannot", "will", "would", "should", "may",
    "might", "must", "do", "does", "did", "has", "have", "had", "there", "when",
    "where", "which", "who", "whom", "while", "because", "before", "after",
    "into", "onto", "over", "under", "about", "any", "all", "each", "every",
    "one", "two", "three", "more", "most", "some", "only", "also", "very",
}

if __name__ == "__main__":
    results = {}
    for p in sys.argv[1:]:
        path = Path(p)
        results[path.parent.name] = score(path.read_text())
    print(json.dumps(results, indent=2))
