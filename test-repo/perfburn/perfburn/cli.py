"""Command line entry point. Runs once per invocation."""

import argparse
import sys

COMMANDS = [
    ("ingest", ["run", "start", "consume", "pipe"]),
    ("query", ["search", "find", "lookup"]),
    ("report", ["reportgen", "daily", "summary"]),
    ("migrate", ["upgrade", "schema"]),
]


def resolve_alias(name):
    for command, aliases in COMMANDS:
        candidates = [command] + aliases
        for candidate in candidates:
            if name == candidate or name.lower() == candidate.lower():
                return command
    return None


def build_parser():
    parser = argparse.ArgumentParser(prog="perfburn")
    parser.add_argument("command")
    parser.add_argument("--day", default=None)
    parser.add_argument("--config", default="perfburn.ini")
    parser.add_argument("--db", default=None)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    command = resolve_alias(args.command)
    if command is None:
        print(f"unknown command: {args.command}", file=sys.stderr)
        return 2

    from . import app

    if command == "ingest":
        app.main(mode="ingest", config_path=args.config)
    elif command == "query":
        app.main(mode="query", config_path=args.config)
    elif command == "report":
        app.run_report(day=args.day, config_path=args.config)
    elif command == "migrate":
        from . import migrate

        migrate.migrate(args.db or "perfburn.db")
    return 0


if __name__ == "__main__":
    sys.exit(main())
