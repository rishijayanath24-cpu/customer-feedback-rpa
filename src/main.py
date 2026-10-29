"""CLI entry point. Exit code 0 = success, 1 = fatal error (so UiPath can detect failure)."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pipeline import run

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Classify customer feedback and route it to departments.")
    p.add_argument("--input", type=Path, nargs="+", default=None,
                   help="one or more feedback CSVs (default: the sample file plus any live "
                        "submissions collected by the web app)")
    p.add_argument("--rules", type=Path, default=ROOT / "config" / "rules.json")
    p.add_argument("--output", type=Path, default=ROOT / "output")
    args = p.parse_args(argv)

    if args.input is None:
        args.input = [ROOT / "data" / "feedback_sample.csv"]
        live = ROOT / "data" / "feedback_live.csv"
        if live.exists():
            args.input.append(live)

    args.output.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(args.output / "routing_log.txt", mode="w", encoding="utf-8"),
        ],
    )
    try:
        run(args.input, args.rules, args.output)
    except Exception:
        logging.getLogger("feedback_rpa").exception("Fatal error")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
