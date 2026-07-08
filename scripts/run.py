"""CLI shim: nightly discover + compress (optionally place a trigger first).

With ``--experiment`` (+ ``--placed-by``) it places a trigger then runs; without
it, it processes whatever triggers are already placed. See
``aeon_raw_compression.cli.run_main``.

Example (nightly, scopes + processes in one command):
    uv run python scripts/run.py --experiment "AEONX1/abcGolden01" --placed-by "$USER"
"""

from aeon_raw_compression.cli import run_main

if __name__ == "__main__":
    raise SystemExit(run_main())
