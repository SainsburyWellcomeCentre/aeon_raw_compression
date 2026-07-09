"""CLI shim: report which raw files are safe to delete (READ-ONLY green-light).

Lists every verified-compressed raw file whose original still exists and has no
``RawEphysFileDeletion`` record, and writes nothing -- take the list to the
recording computer (the only machine with Ceph delete rights) and delete those
paths there. See ``aeon_raw_compression.cli.report_deletable_main``.

Example:
    uv run python scripts/report_deletable.py
"""

from aeon_raw_compression.cli import report_deletable_main

if __name__ == "__main__":
    raise SystemExit(report_deletable_main())
