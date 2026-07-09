"""CLI shim: place a RawEphysDiscoveryTrigger. See ``cli.add_trigger_main``.

Example:
    uv run python scripts/add_trigger.py --experiment "AEONX1/abcGolden01" --placed-by "$USER"

    # restrict to a single epoch:
    uv run python scripts/add_trigger.py \\
        --experiment "AEONX1/abcGolden01" --epoch "2026-05-11T07-50-11" --placed-by "$USER"
"""

from aeon_raw_compression.cli import add_trigger_main

if __name__ == "__main__":
    raise SystemExit(add_trigger_main())
