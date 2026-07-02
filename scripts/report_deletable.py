"""CLI: report which raw files are safe to delete (READ-ONLY green-light).

Lists every verified-compressed raw file whose original still exists and has no
``RawEphysFileDeletion`` record. It **writes nothing** -- it is the manual
green-light: take the list to the recording computer (the only machine with Ceph
delete rights) and delete those paths there.

Example:
    uv run python scripts/report_deletable.py --placed-by "$USER"
"""

import argparse

from aeon_raw_compression import pipeline
from aeon_raw_compression.cli import build_populate_restriction, format_deletable_report


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Report raw files that are safe to delete (read-only; deletes nothing)."
    )
    parser.add_argument(
        "--placed-by", default=None, help="restrict to triggers placed by this user"
    )
    parser.add_argument(
        "--prefix", default=None, help="DataJoint prefix (default: project's configured prefix)"
    )
    args = parser.parse_args(argv)

    pipeline.activate(args.prefix)
    rows = pipeline.report_deletable(build_populate_restriction(args.placed_by))
    print(format_deletable_report(rows))


if __name__ == "__main__":
    main()
