"""Small command-line entry point for the delivered Case-1 simulator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from wus_next.contracts import load_profile


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m wus_next.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="validate one evidence-aware project profile")
    validate.add_argument("--profile", required=True)
    args = parser.parse_args(argv)
    if args.command == "validate":
        profile_path = Path(args.profile).resolve()
        profile = load_profile(profile_path)
        print(
            json.dumps(
                {
                    "status": "PASS",
                    "profile": str(profile_path),
                    "profile_id": profile["profile_id"],
                    "contract_version": profile["contract_version"],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
