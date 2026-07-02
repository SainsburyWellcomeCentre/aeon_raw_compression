"""CLI: run discovery + compress/verify (cron / SLURM entry point).

Populates ``RawEphysDiscovery`` then ``CompressedFile`` for placed triggers.
**Never** touches ``RawEphysFileDeletion`` -- deletion is hard-disabled in source
and has no CLI by design.

Example (nightly):
    uv run python scripts/run.py --placed-by elissa
"""

import argparse
import sys

from aeon_raw_compression import pipeline
from aeon_raw_compression.cli import build_populate_restriction, summarize_run


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Discover and compress (with verification) raw ephys files."
    )
    parser.add_argument(
        "--placed-by", default=None, help="restrict to triggers placed by this user"
    )
    parser.add_argument(
        "--prefix", default=None, help="DataJoint prefix (default: project's configured prefix)"
    )
    parser.add_argument(
        "--max-calls", type=int, default=None, help="cap populate calls per stage (testing)"
    )
    args = parser.parse_args(argv)

    pipeline.activate(args.prefix)
    restriction = build_populate_restriction(args.placed_by)

    populate_kwargs = {"reserve_jobs": True, "suppress_errors": True}
    if args.max_calls is not None:
        populate_kwargs["max_calls"] = args.max_calls

    pipeline.RawEphysDiscovery.populate(restriction, **populate_kwargs)
    pipeline.CompressedFile.populate(restriction, **populate_kwargs)
    # Intentionally no RawEphysFileDeletion.populate(): deletion is disabled in v1.

    num_registered = len(pipeline.RawEphysDiscovery.RawEphysFile & restriction)
    num_compressed = len(pipeline.CompressedFile & restriction)
    msg, failed = summarize_run(num_registered, num_compressed)
    print(f"[aeon_raw_compression] {msg}")
    if failed:
        print(
            "[aeon_raw_compression] some files registered but did not compress; "
            "inspect CompressedFile.jobs for errors.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
