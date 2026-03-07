from __future__ import annotations

import argparse
from collections.abc import Sequence

from pr_agent_context.config import RunConfig
from pr_agent_context.services.run import run_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr-agent-context")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Collect PR context and manage the PR comment.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        config = RunConfig.from_env()
        return run_service(config)
    parser.error(f"Unsupported command: {args.command}")
    return 2
