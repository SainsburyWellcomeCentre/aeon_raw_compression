"""CLI: place a RawEphysDiscoveryTrigger.

Example:
    uv run python scripts/add_trigger.py \\
        --experiment "AEONX1/abcGolden01" --placed-by elissa

    # restrict to a single epoch:
    uv run python scripts/add_trigger.py \\
        --experiment "AEONX1/abcGolden01" --epoch "2026-05-11T07-50-11" \\
        --placed-by elissa
"""

import argparse

from aeon_raw_compression import pipeline
from aeon_raw_compression.cli import build_trigger_row


def main(argv=None):
    parser = argparse.ArgumentParser(description="Place a raw-ephys compression discovery trigger.")
    parser.add_argument(
        "--experiment", required=True, help='directory in scope, e.g. "AEONX1/abcGolden01"'
    )
    parser.add_argument("--epoch", default="", help="optional: restrict to one epoch directory")
    parser.add_argument("--placed-by", required=True, help="who is placing this trigger")
    parser.add_argument(
        "--prefix", default=None, help="DataJoint prefix (default: project's configured prefix)"
    )
    args = parser.parse_args(argv)

    pipeline.activate(args.prefix)
    row = build_trigger_row(args.experiment, args.placed_by, args.epoch)
    pipeline.RawEphysDiscoveryTrigger.insert1(row)
    print(f"Placed trigger: {row}")


if __name__ == "__main__":
    main()
