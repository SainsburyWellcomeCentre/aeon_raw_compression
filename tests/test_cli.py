"""Unit tests for the pure CLI helpers (no DB).

The DB-touching parts of the scripts (activate, insert, populate) are exercised
by the HPC integration tests; here we only pin the pure row/restriction builders.
"""

import datetime

from aeon_raw_compression.cli import build_populate_restriction, build_trigger_row


def test_build_trigger_row_with_explicit_time():
    t = datetime.datetime(2026, 6, 26, 14, 0, 0)
    row = build_trigger_row(
        "AEONX1/exp", "elissa", epoch="2026-05-11T07-50-11", trigger_time=t
    )
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
