"""Unit tests for the pure CLI helpers (no DB).

The DB-touching parts of the scripts (activate, insert, populate) are exercised
by the HPC integration tests; here we only pin the pure row/restriction builders.
"""

import datetime

from aeon_raw_compression.cli import (
    build_populate_restriction,
    build_trigger_row,
    format_deletable_report,
    summarize_run,
)


def test_build_trigger_row_with_explicit_time():
    t = datetime.datetime(2026, 6, 26, 14, 0, 0)
    row = build_trigger_row("AEONX1/exp", "elissa", epoch="2026-05-11T07-50-11", trigger_time=t)
    assert row == {
        "trigger_time": t,
        "placed_by": "elissa",
        "experiment_path": "AEONX1/exp",
        "epoch_path": "2026-05-11T07-50-11",
    }


def test_build_trigger_row_defaults_epoch_empty_and_time_now():
    row = build_trigger_row("AEONX1/exp", "elissa")
    assert row["epoch_path"] == ""
    assert row["experiment_path"] == "AEONX1/exp"
    assert row["placed_by"] == "elissa"
    assert isinstance(row["trigger_time"], datetime.datetime)


def test_build_populate_restriction_with_user():
    assert build_populate_restriction("elissa") == {"placed_by": "elissa"}


def test_build_populate_restriction_without_user():
    assert build_populate_restriction() == {}
    assert build_populate_restriction(None) == {}


def test_summarize_run_flags_failures():
    msg, failed = summarize_run(5, 5)
    assert "errored=0" in msg and failed is False
    msg, failed = summarize_run(5, 3)
    assert "errored=2" in msg and failed is True


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
