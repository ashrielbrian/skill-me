"""Shared argparse plumbing for the scripts under tools/.

Every script here runs manually or from CI, never in the serving path.
"""

from __future__ import annotations

import argparse


def build_parser(description: str | None) -> argparse.ArgumentParser:
    """Parser with the flags every writing maintenance script shares."""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without writing.",
    )
    return parser


def confirm(prompt: str, assume_yes: bool = False) -> bool:
    """Interactive yes/no gate for commands that rewrite whole tables."""
    if assume_yes:
        return True
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in {"y", "yes"}
