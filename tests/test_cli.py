"""Unit tests for the pure CLI helper (no DB).

The CLI is thin argparse wrappers (``cli.add_trigger_main`` / ``run_main`` /
``report_deletable_main``) over the tested :mod:`aeon_raw_compression.pipeline`
API; those DB-touching entry points are exercised by the HPC integration tests.
Here we only pin the one pure, DB-free helper: the deletable-report formatter.
"""

from aeon_raw_compression.cli import format_deletable_report


def test_format_deletable_report_lists_paths_and_total():
    rows = [
        {"file_path": "/raw/a_0.bin", "file_size_bytes": 2_000_000_000},
        {"file_path": "/raw/b_0.bin", "file_size_bytes": 1_000_000_000},
    ]
    msg = format_deletable_report(rows)
    assert "/raw/a_0.bin" in msg and "/raw/b_0.bin" in msg
    assert "2 files" in msg
    assert "3.0 GB" in msg  # 2 GB + 1 GB reclaimable


def test_format_deletable_report_empty():
    assert format_deletable_report([]) == "0 files eligible for deletion."
