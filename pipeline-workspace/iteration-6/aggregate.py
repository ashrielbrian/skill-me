#!/usr/bin/env python3
"""Aggregate every run into benchmark.json and print a per-arm comparison table.

Iteration 6 evaluates the new perform-like-jeff-dean skill. Arms per target:
  base  a 2-line stub prompt (the floor: what the model does with no skill body)
  v1..  successive versions of the skill; after the first commit, the committed
        version becomes the baseline arm per the repo protocol
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from ste_score import score as ste_score            # noqa: E402
from structure_score import score as struct_score   # noqa: E402
from detail_score import score as detail            # noqa: E402
import gt_score                                     # noqa: E402
import priority_score                               # noqa: E402

IT = Path(__file__).parent
GT_PB = json.loads((IT / "ground_truth_perfburn.json").read_text())
_EB = json.loads((IT.parent / "iteration-4" / "ground_truth_eventbus.json").read_text())
# Eventbus is the secondary target: only its 4 planted Performance issues are
# in scope for a perf audit; the traps still apply.
GT_EB_PERF = {
    "issues": [i for i in _EB["issues"] if i["category"] == "Performance"],
    "total_planted": sum(1 for i in _EB["issues"] if i["category"] == "Performance"),
    "false_positive_traps": _EB.get("false_positive_traps", []),
}

# Runs against private repositories. This repo is public, and the scorers embed
# example sentences lifted from the reports they score, so these runs go to a
# gitignored file. They still appear in the printed table for local review.
PRIVATE_PREFIXES = ("pr-private",)

rows = {}
for run in sorted((IT / "runs").iterdir()):
    if run.name == "smoke" or not (run / "report.md").exists():
        continue
    md = (run / "report.md").read_text()
    if not md.strip():
        rows[run.name] = {"error": "empty report"}
        continue
    t = json.loads((run / "timing.json").read_text())
    row = {
        "timing": {k: t[k] for k in ("wall_seconds", "num_turns", "cost_usd",
                                     "output_tokens", "total_tokens",
                                     "permission_denials", "is_error")},
        "structure": struct_score(run.name, md),
        "ste": ste_score(md),
        "detail": detail(md),
    }
    if run.name.startswith("perf-perfburn"):
        row["ground_truth"] = gt_score.score(
            md, GT_PB, line_window=GT_PB.get("line_window", 12))
        row["priority"] = priority_score.score(md, GT_PB)
    if run.name.startswith("perf-eventbus"):
        row["ground_truth"] = gt_score.score(md, GT_EB_PERF)
    rows[run.name] = row

public = {k: v for k, v in rows.items() if not k.startswith(PRIVATE_PREFIXES)}
private = {k: v for k, v in rows.items() if k.startswith(PRIVATE_PREFIXES)}
(IT / "benchmark.json").write_text(json.dumps(public, indent=2))
if private:
    (IT / "benchmark-private.json").write_text(json.dumps(private, indent=2))

GROUPS, ARMS = {}, ("base", "v1", "v1-b", "v2", "v2-b", "v3", "v3-b", "v4", "v4-b")
for name in rows:
    for arm in ARMS:
        if name.endswith("-" + arm):
            GROUPS.setdefault(name[: -(len(arm) + 1)], {})[arm] = rows[name]

METRICS = [
    ("template_pass_rate", lambda r: r["structure"]["template_pass_rate"]),
    ("missing_headings", lambda r: len(r["structure"]["missing_headings"])),
    ("missing_fields", lambda r: len(r["structure"]["missing_fields"])),
    ("severity_case", lambda r: r["structure"]["severity_case"] or "-"),
    ("findings", lambda r: r["structure"]["findings_count"]),
    (". STE conformance", lambda r: ""),
    ("mean_sentence_words", lambda r: r["ste"]["mean_sentence_words"]),
    ("max_sentence_words", lambda r: r["ste"]["max_sentence_words"]),
    ("over_25_rate", lambda r: r["ste"]["over_25_rate"]),
    ("over_20_rate", lambda r: r["ste"]["over_20_rate"]),
    ("passive_per_100_sent", lambda r: r["ste"]["passive_per_100_sentences"]),
    ("non_approved_words", lambda r: r["ste"]["non_approved_total"]),
    ("idioms", lambda r: r["ste"]["idiom_total"]),
    ("ing_openers", lambda r: r["ste"]["ing_opener_total"]),
    (". information density", lambda r: ""),
    ("chars_per_finding", lambda r: r["detail"]["chars_per_finding"]),
    ("citations_per_finding", lambda r: r["detail"]["citations_per_finding"]),
    ("quantities_per_finding", lambda r: r["detail"]["quantities_per_finding"]),
    ("code_refs_per_finding", lambda r: r["detail"]["code_refs_per_finding"]),
    ("causal_per_finding", lambda r: r["detail"]["causal_per_finding"]),
    (". task quality", lambda r: ""),
    # For perfburn, recall comes from priority_score: it is section-aware (a
    # cold issue recorded in Cold-Code Notes counts as found, and the trailing
    # sections do not bleed into the last finding block).
    ("recall", lambda r: (r["priority"]["recall_total"] if "priority" in r
                          else r.get("ground_truth", {}).get("recall", "-"))),
    ("recall_ranked", lambda r: r.get("priority", {}).get("recall_ranked", "-")),
    ("planted_found", lambda r: r.get("ground_truth", {}).get("planted_found", "-")),
    (". prioritization", lambda r: ""),
    ("pairwise_order_acc", lambda r: r.get("priority", {}).get("pairwise_order_accuracy", "-")),
    ("top3_mean_tier", lambda r: r.get("priority", {}).get("top3_mean_expected_tier", "-")),
    ("impact_label_acc", lambda r: r.get("priority", {}).get("impact_label_accuracy", "-")),
    ("effort_label_acc", lambda r: r.get("priority", {}).get("effort_label_accuracy", "-")),
    ("tier_label_acc", lambda r: r.get("priority", {}).get("tier_label_accuracy", "-")),
    ("strict_precision", lambda r: r.get("priority", {}).get("strict_precision", "-")),
    ("fp_trap_hits", lambda r: r.get("priority", {}).get("fp_trap_hits", "-")),
    ("ranked_findings", lambda r: r.get("priority", {}).get("ranked_findings", "-")),
    (". cost", lambda r: ""),
    ("wall_seconds", lambda r: r["timing"]["wall_seconds"]),
    ("output_tokens", lambda r: r["timing"]["output_tokens"]),
    ("cost_usd", lambda r: round(r["timing"]["cost_usd"], 2)),
]

for group, arms in GROUPS.items():
    present = [a for a in ARMS if a in arms]
    print(f"\n=== {group}   ({', '.join(present)})")
    print(f"  {'metric':<24}" + "".join(f"{a:>14}" for a in present))
    for label, fn in METRICS:
        if label.startswith("."):
            print(f"  {label[1:].strip():-<24}")
            continue
        cells = []
        for a in present:
            try:
                cells.append(fn(arms[a]))
            except (KeyError, TypeError):
                cells.append("-")
        print(f"  {label:<24}" + "".join(f"{str(c):>14}" for c in cells))

print(f"\nbenchmark.json -> {IT / 'benchmark.json'}")
