#!/usr/bin/env python3
"""Render a SKILL.md into a standalone headless prompt.

Strips YAML frontmatter and substitutes $ARGUMENTS so the skill body can be fed
to `claude -p` directly. This keeps the eval independent of skill installation,
so the current working-tree version and the previous commit's version can be run
side by side without touching ~/.claude/skills.
"""
import sys
from pathlib import Path

skill_path, arguments, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
text = Path(skill_path).read_text()

if text.startswith("---"):
    end = text.index("\n---", 3)
    text = text[end + 4 :].lstrip("\n")

text = text.replace("$ARGUMENTS", arguments if arguments.strip() else "(none)")

# The skill bodies end with the report template; make the delivery contract
# explicit for headless runs, where there is no interactive turn to fall back on.
text += (
    "\n\n---\n\nOutput the complete report as your final message. "
    "Do not write it to a file. Do not ask any clarifying questions.\n"
)
Path(out_path).write_text(text)
print(f"{out_path}: {len(text)} chars")
