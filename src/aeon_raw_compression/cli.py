"""Thin, importable CLI entry points over the pipeline API.

Each ``*_main(argv=None)`` parses args, activates the schema, and calls the
tested :mod:`aeon_raw_compression.pipeline` API. The ``scripts/*.py`` files are
2-line shims over these (so ``python scripts/run.py`` + the SLURM template keep
working without an install), and tests can call the mains with an ``argv`` list.
The only pure, DB-free helper is :func:`format_deletable_report`.
"""

import argparse
import sys

from aeon_raw_compression import pipeline


def format_deletable_report(rows):
    """Render the read-only "green-light" deletion report.

    ``rows`` is the output of :func:`pipeline.report_deletable` -- dicts with
    ``file_path`` and ``file_size_bytes``. Returns a human-readable message
    (paths + total reclaimable GB); the caller prints it. Nothing is deleted --
    the user manually deletes these paths on the recording computer.
    """
    if not rows:
        return "0 files eligible for deletion."
    total_gb = sum(r["file_size_bytes"] for r in rows) / 1e9
    lines = [f"{len(rows)} files, {total_gb:.1f} GB reclaimable:"]
    lines += [f"  {r['file_path']}" for r in rows]
    return "\n".join(lines)


def add_trigger_main(argv=None):
    """CLI: place a discovery trigger scoping a directory to scan."""
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
    pipeline.add_trigger(args.experiment, args.placed_by, args.epoch)
    print(f"Placed trigger: {args.experiment} (placed_by={args.placed_by})")


def run_main(argv=None):
    """CLI: discover + compress (with verification). The nightly entry point.

    With ``--experiment`` it first places a trigger (``--placed-by`` then
    required) and then runs -- so the nightly job both scopes and processes the
    day's recordings in one command. Without it, it just processes whatever
    triggers are already placed. Returns a shell exit code (1 if any registered
    file failed to compress).
    """
    parser = argparse.ArgumentParser(
        description="Discover and compress (with verification) raw ephys files."
    )
    parser.add_argument(
        "--experiment", default=None, help="if given, place a trigger for it first (nightly)"
    )
    parser.add_argument("--placed-by", default=None, help="required when --experiment is given")
    parser.add_argument(
        "--prefix", default=None, help="DataJoint prefix (default: project's configured prefix)"
    )
    parser.add_argument(
        "--max-calls", type=int, default=None, help="cap populate calls per stage (testing)"
    )
    args = parser.parse_args(argv)
    if args.experiment and not args.placed_by:
        parser.error("--placed-by is required when --experiment is given")

    pipeline.activate(args.prefix)
    if args.experiment:
        pipeline.add_trigger(args.experiment, args.placed_by)
    summary = pipeline.run(max_calls=args.max_calls)
    print(f"[aeon_raw_compression] {summary}")
    if summary.num_errored:
        print(
            "[aeon_raw_compression] some files registered but did not compress; "
            "inspect CompressedFile.jobs for errors.",
            file=sys.stderr,
        )
        return 1
    return 0


def report_deletable_main(argv=None):
    """CLI: report raw files safe to delete (read-only; deletes nothing)."""
    parser = argparse.ArgumentParser(
        description="Report raw files that are safe to delete (read-only; deletes nothing)."
    )
    parser.add_argument(
        "--prefix", default=None, help="DataJoint prefix (default: project's configured prefix)"
    )
    args = parser.parse_args(argv)

    pipeline.activate(args.prefix)
    print(format_deletable_report(pipeline.report_deletable()))
